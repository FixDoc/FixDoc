"""Rebuildable SQLite index, with staged embeddings and atomic publication."""

import hashlib
import json
import math
import os
import re
import time
import uuid
from array import array
from collections import defaultdict, namedtuple
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .database import SCHEMA, SCHEMA_VERSION, open_database, transaction
from .models import TYPE_PREFIXES, Entry

_ENTRY_FILENAME_RE = re.compile(r"^(?:%s)_[0-9a-f]{8}\.md$" % "|".join(TYPE_PREFIXES.values()))
Candidate = namedtuple("Candidate", "id type resource_type")
Row = namedtuple(
    "Row",
    "id path type status resource_type title occurrences confidence "
    "created env_scope match_keys vector",
)


def _has_table(connection, name):
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()
        is not None
    )


def _meta_values(connection):
    if not _has_table(connection, "meta"):
        return {}
    return dict(connection.execute("SELECT key, value FROM meta"))


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _entry_paths(store_dir):
    # Path.rglob can suppress directory permission errors, which would look
    # like deleted entries. An incomplete scan must never be published.
    def onerror(error):
        raise error

    return sorted(
        Path(root) / name
        for root, directories, files in os.walk(store_dir, onerror=onerror)
        for name in files
        if _ENTRY_FILENAME_RE.match(name)
    )


def _validate(entry, path):
    if not isinstance(entry.id, str) or entry.id != path.stem:
        raise ValueError("frontmatter id must match the filename")
    if not isinstance(entry.type, str) or entry.type not in TYPE_PREFIXES:
        raise ValueError("unknown entry type")
    if not isinstance(entry.title, str) or not entry.title.strip():
        raise ValueError("title must be a nonempty string")
    if type(entry.occurrences) is not int or not 0 <= entry.occurrences <= 2**63 - 1:
        raise ValueError("occurrences must be a nonnegative SQLite integer")
    if entry.confidence is not None and (
        type(entry.confidence) not in (int, float)
        or not math.isfinite(entry.confidence)
        or not 0 <= entry.confidence <= 1
    ):
        raise ValueError("confidence must be between 0 and 1")
    if not isinstance(entry.env_scope, list) or any(
        not isinstance(v, str) for v in entry.env_scope
    ):
        raise ValueError("env_scope must be a list of strings")
    if not isinstance(entry.match_keys, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in entry.match_keys.items()
    ):
        raise ValueError("match_keys must be a string map")
    if entry.resource_type is not None and not isinstance(entry.resource_type, str):
        raise ValueError("resource_type must be a string")
    problems = entry.validate()
    if problems:
        raise ValueError("; ".join(problems))


class Index:
    def __init__(self, index_dir, embed_fn, model_name):
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.embed_fn = embed_fn
        self.model_name = model_name
        self.db = open_database(self.index_dir / "index.db")
        # Opening an Index never invalidates committed data. A model/schema
        # change takes effect only when sync publishes a complete replacement.

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _compatible(self, values):
        return (
            values.get("schema_version") == SCHEMA_VERSION
            and values.get("embedding_model") == self.model_name
        )

    def sync(self, store_dir, *, rebuild=False):
        """Publish a file snapshot atomically; retry if another writer won.

        Skipped files are excluded (including previously indexed malformed
        files). Fatal I/O, embedding or database errors preserve committed
        rows and run metadata. Missing populated stores are never treated as
        deletion of every entry.
        """
        store_dir = Path(store_dir)
        started = time.monotonic()
        for attempt in range(3):
            with transaction(self.db) as connection:
                previous = _meta_values(connection)
                compatible = self._compatible(previous)
                known = (
                    {
                        r["id"]: dict(r)
                        for r in connection.execute(
                            "SELECT id, path, content_hash, search_hash FROM entries"
                        )
                    }
                    if compatible
                    else {}
                )
            # Preserve MCP's empty-store behavior. A missing populated store
            # is an error, never interpreted as deletion of every entry.
            if not store_dir.exists() and (not previous or (compatible and not known)):
                paths = []
            else:
                paths = _entry_paths(store_dir)
            full = rebuild or not compatible
            rows, stats = self._prepare(store_dir, known, full, paths, previous)
            if rows is None:
                continue
            with transaction(self.db, writer=True) as connection:
                if _meta_values(connection) != previous:
                    continue
                self._publish(connection, rows, known, previous, full)
                report = {
                    "operation": "rebuild" if full else "sync",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "duration_seconds": round(time.monotonic() - started, 6),
                    **stats,
                }
                values = {
                    "schema_version": SCHEMA_VERSION,
                    "embedding_model": self.model_name,
                    "generation": uuid.uuid4().hex,
                    "last_run": json.dumps(report),
                }
                connection.execute("DELETE FROM meta")
                connection.executemany(
                    "INSERT INTO meta (key, value) VALUES (?, ?)", values.items()
                )
            return stats
        raise RuntimeError(
            "Index changed during three sync attempts; retry when other writers finish."
        )

    def _prepare(self, store_dir, known, full, paths, previous):
        stats = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0, "skipped": []}
        errors, prepared, claims = {}, {}, defaultdict(list)
        for path in paths:
            rel = path.relative_to(store_dir).as_posix()
            try:
                text = path.read_text(encoding="utf-8")
                content_hash = _hash(text)
                old = known.get(path.stem)
                if old and old["content_hash"] == content_hash and old["path"] == rel and not full:
                    claims[old["id"]].append(rel)
                    prepared[rel] = (dict(old, path=rel), None)
                    continue
                entry = Entry.from_markdown(text)
                if isinstance(entry.id, str):
                    claims[entry.id].append(rel)
                _validate(entry, path)
                search_text = entry.search_text()
                row = {
                    "id": entry.id,
                    "path": rel,
                    "content_hash": content_hash,
                    "search_hash": _hash(search_text),
                    **{
                        key: getattr(entry, key)
                        for key in (
                            "type",
                            "status",
                            "resource_type",
                            "title",
                            "occurrences",
                            "confidence",
                            "created",
                            "env_scope",
                            "match_keys",
                        )
                    },
                }
                prepared[rel] = (row, search_text)
            except yaml.YAMLError:
                errors[rel] = "invalid YAML frontmatter"
            except UnicodeError:
                errors[rel] = "entry must be UTF-8 text"
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                errors[rel] = str(exc)
        for entry_id, paths in claims.items():
            if len(paths) > 1:
                for rel in paths:
                    errors[rel] = f"duplicate id {entry_id}: {', '.join(paths)}"

        rows, reuse, to_embed = {}, [], []
        unchanged_id = None
        for rel, (row, search_text) in prepared.items():
            if rel in errors:
                continue
            old = known.get(row["id"])
            unchanged = old == row and not full
            if unchanged:
                unchanged_id = row["id"]
            elif old and old["search_hash"] == row["search_hash"] and not full:
                reuse.append(row)
            else:
                to_embed.append((rel, row, search_text))
            rows[row["id"]] = row
            stats["unchanged" if unchanged else "updated" if old else "added"] += 1

        dimensions = set()
        if reuse or (unchanged_id is not None and to_embed):
            with transaction(self.db) as connection:
                # Cached vectors must belong to the same generation as the hashes.
                if _meta_values(connection) != previous:
                    return None, stats
                for row in reuse:
                    row["embedding"] = connection.execute(
                        "SELECT embedding FROM entries WHERE id = ?", (row["id"],)
                    ).fetchone()[0]
                if unchanged_id is not None:
                    # Published vectors have a uniform dimension; read only its size.
                    dimensions.add(
                        connection.execute(
                            "SELECT length(embedding) FROM entries WHERE id = ?", (unchanged_id,)
                        ).fetchone()[0]
                    )
        # Model inference stays outside transactions so other writers can commit.
        for rel, row, search_text in to_embed:
            vector = array("f", self.embed_fn(search_text))
            if not vector or any(not math.isfinite(v) for v in vector):
                raise ValueError(f"Embedding for {rel} must be a nonempty finite vector")
            row["embedding"] = vector.tobytes()
        dimensions.update(len(row["embedding"]) for row in rows.values() if "embedding" in row)
        if len(dimensions) > 1:
            raise ValueError("Embedding dimensions differ; check the model and run index --rebuild")
        stats["removed"] = len(known.keys() - rows.keys())
        stats["skipped"] = sorted(errors)
        stats["errors"] = errors
        return rows, stats

    def _publish(self, connection, rows, known, previous, full):
        if previous.get("schema_version") != SCHEMA_VERSION:
            connection.execute("DROP TABLE IF EXISTS entries")
        for statement in SCHEMA:
            connection.execute(statement)
        if full:
            connection.execute("DELETE FROM entries")
            changed = list(rows.values())
        else:
            # Delete changed rows before inserting, so path moves cannot
            # collide with old unique paths. Executemany avoids SQLite's
            # variable limit even for large stores.
            affected = [key for key in known if rows.get(key) != known[key]]
            if affected:
                connection.executemany(
                    "DELETE FROM entries WHERE id = ?", [(key,) for key in affected]
                )
            changed = [row for key, row in rows.items() if known.get(key) != row]
        if changed:
            connection.executemany(
                "INSERT INTO entries (id, path, content_hash, search_hash, type, status, "
                "resource_type, title, occurrences, confidence, created, env_scope, match_keys, "
                "embedding) VALUES (:id, :path, :content_hash, :search_hash, :type, :status, "
                ":resource_type, :title, :occurrences, :confidence, :created, :env_scope, "
                ":match_keys, :embedding)",
                [
                    dict(
                        row,
                        env_scope=json.dumps(row["env_scope"]),
                        match_keys=json.dumps(row["match_keys"]),
                    )
                    for row in changed
                ],
            )

    def _read(self, statement, parameters=()):
        with transaction(self.db) as connection:
            if not self._compatible(_meta_values(connection)):
                return []
            return connection.execute(statement, parameters).fetchall()

    def candidates(self, entry_type):
        """(Candidate, vector) pairs for dedup: live entries of one type."""
        rows = self._read(
            "SELECT id, type, resource_type, embedding FROM entries "
            "WHERE type = ? AND status NOT IN ('deprecated', 'rejected') ORDER BY id",
            (entry_type,),
        )
        return [(Candidate(*r[:3]), list(array("f", r[3]))) for r in rows]

    def path_for(self, entry_id):
        rows = self._read("SELECT path FROM entries WHERE id = ?", (entry_id,))
        return rows[0][0] if rows else None

    def live(self, entry_type=None, include_quarantined=False):
        """Full rows for retrieval: validated (optionally + quarantined)."""
        statuses = ["validated"] + (["quarantined"] if include_quarantined else [])
        query = (
            "SELECT id, path, type, status, resource_type, title, occurrences, confidence, "
            "created, env_scope, match_keys, embedding FROM entries "
            "WHERE status IN (%s)" % ",".join("?" for _ in statuses)
        )
        parameters = list(statuses)
        if entry_type:
            query += " AND type = ?"
            parameters.append(entry_type)
        return [
            Row(
                *r[:9], json.loads(r[9] or "[]"), json.loads(r[10] or "{}"), list(array("f", r[11]))
            )
            for r in self._read(query + " ORDER BY id", parameters)
        ]


def index_stats(index_dir):
    """Inspect committed data without initializing, rebuilding or embedding."""
    path = Path(index_dir).resolve() / "index.db"
    result = {"exists": path.is_file(), "path": str(path)}
    if not result["exists"]:
        return result
    with (
        closing(open_database(path, readonly=True)) as database,
        transaction(database) as connection,
    ):
        values = _meta_values(connection)
        counts = {"total": 0, "by_type": {}, "by_status": {}}
        if _has_table(connection, "entries"):
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(entries)")}
            counts["total"] = connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            for field in ("type", "status"):
                if field in columns:
                    counts[f"by_{field}"] = dict(
                        connection.execute(
                            f"SELECT {field}, COUNT(*) FROM entries GROUP BY {field}"
                        )
                    )
        result.update(
            size_bytes=path.stat().st_size,
            schema_version=values.get("schema_version"),
            embedding_model=values.get("embedding_model"),
            needs_rebuild=values.get("schema_version") != SCHEMA_VERSION,
            entries=counts,
            last_run=json.loads(values["last_run"]) if values.get("last_run") else None,
        )
    return result
