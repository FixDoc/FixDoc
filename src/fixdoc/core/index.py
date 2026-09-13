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
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import MetaData, Table, bindparam, func, inspect, select

from .database import SCHEMA_VERSION, entries, meta, metadata, open_engine
from .models import TYPE_PREFIXES, Entry

_ENTRY_FILENAME_RE = re.compile(r"^(?:%s)_[0-9a-f]{8}\.md$" % "|".join(TYPE_PREFIXES.values()))
Candidate = namedtuple("Candidate", "id type resource_type")
Row = namedtuple(
    "Row",
    "id path type status resource_type title occurrences confidence "
    "created env_scope match_keys vector",
)


def _meta_values(connection):
    if not inspect(connection).has_table("meta"):
        return {}
    return dict(connection.execute(select(meta)).tuples().all())


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
    if entry.status not in ("validated", "quarantined", "deprecated", "rejected"):
        raise ValueError("unknown entry status")
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
        self.engine = open_engine(self.index_dir / "index.db")
        # Opening an Index never invalidates committed data. A model/schema
        # change takes effect only when sync publishes a complete replacement.

    def close(self):
        self.engine.dispose()

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
            with self.engine.connect() as connection:
                previous = _meta_values(connection)
                compatible = self._compatible(previous)
                known = (
                    {r["id"]: dict(r) for r in connection.execute(select(entries)).mappings()}
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
            rows, stats = self._prepare(store_dir, known, full, paths)
            with self.engine.connect().execution_options(writer=True) as connection:
                with connection.begin():
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
                    connection.execute(meta.delete())
                    connection.execute(
                        meta.insert(), [{"key": k, "value": v} for k, v in values.items()]
                    )
                return stats
        raise RuntimeError(
            "Index changed during three sync attempts; retry when other writers finish."
        )

    def _prepare(self, store_dir, known, full, paths):
        stats = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0, "skipped": []}
        errors, prepared, claims = {}, {}, defaultdict(list)
        for path in paths:
            rel = path.relative_to(store_dir).as_posix()
            try:
                text = path.read_text(encoding="utf-8")
                content_hash = _hash(text)
                old = known.get(path.stem)
                if old and old["content_hash"] == content_hash and not full:
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

        rows = {}
        for rel, (row, search_text) in prepared.items():
            if rel in errors:
                continue
            old = known.get(row["id"])
            unchanged = old == row and not full
            if "embedding" not in row:
                if old and old["search_hash"] == row["search_hash"] and not full:
                    row["embedding"] = old["embedding"]
                else:
                    vector = array("f", self.embed_fn(search_text))
                    if not vector or any(not math.isfinite(v) for v in vector):
                        raise ValueError(f"Embedding for {rel} must be a nonempty finite vector")
                    row["embedding"] = vector.tobytes()
            rows[row["id"]] = row
            stats["unchanged" if unchanged else "updated" if old else "added"] += 1
        if len({len(row["embedding"]) for row in rows.values()}) > 1:
            raise ValueError("Embedding dimensions differ; check the model and run index --rebuild")
        stats["removed"] = len(known.keys() - rows.keys())
        stats["skipped"] = sorted(errors)
        stats["errors"] = errors
        return rows, stats

    def _publish(self, connection, rows, known, previous, full):
        if previous.get("schema_version") != SCHEMA_VERSION:
            entries.drop(connection, checkfirst=True)
        metadata.create_all(connection)
        if full:
            connection.execute(entries.delete())
            changed = list(rows.values())
        else:
            # Delete changed rows before inserting, so path moves cannot
            # collide with old unique paths. Executemany avoids SQLite's
            # variable limit even for large stores.
            affected = [key for key in known if rows.get(key) != known[key]]
            if affected:
                connection.execute(
                    entries.delete().where(entries.c.id == bindparam("entry_id")),
                    [{"entry_id": key} for key in affected],
                )
            changed = [row for key, row in rows.items() if known.get(key) != row]
        if changed:
            connection.execute(entries.insert(), changed)

    def _read(self, statement):
        with self.engine.connect() as connection:
            if not self._compatible(_meta_values(connection)):
                return []
            return connection.execute(statement).all()

    def candidates(self, entry_type):
        """(Candidate, vector) pairs for dedup: live entries of one type."""
        query = (
            select(entries.c.id, entries.c.type, entries.c.resource_type, entries.c.embedding)
            .where(
                entries.c.type == entry_type,
                entries.c.status.not_in(("deprecated", "rejected")),
            )
            .order_by(entries.c.id)
        )
        return [(Candidate(*r[:3]), list(array("f", r[3]))) for r in self._read(query)]

    def path_for(self, entry_id):
        rows = self._read(select(entries.c.path).where(entries.c.id == entry_id))
        return rows[0][0] if rows else None

    def live(self, entry_type=None, include_quarantined=False):
        """Full rows for retrieval: validated (optionally + quarantined)."""
        statuses = ["validated"] + (["quarantined"] if include_quarantined else [])
        fields = list(Row._fields[:-1])
        query = (
            select(*(entries.c[key] for key in fields), entries.c.embedding)
            .where(entries.c.status.in_(statuses))
            .order_by(entries.c.id)
        )
        if entry_type:
            query = query.where(entries.c.type == entry_type)
        return [Row(*r[:-1], list(array("f", r[-1]))) for r in self._read(query)]


def index_stats(index_dir):
    """Inspect committed data without initializing, rebuilding or embedding."""
    path = Path(index_dir).resolve() / "index.db"
    result = {"exists": path.is_file(), "path": str(path)}
    if not result["exists"]:
        return result
    engine = open_engine(path, readonly=True)
    try:
        with engine.connect() as connection:
            values = _meta_values(connection)
            # Reflect for inspection only: older schemas remain observable.
            counts = {"total": 0, "by_type": {}, "by_status": {}}
            if inspect(connection).has_table("entries"):
                table = Table("entries", MetaData(), autoload_with=connection)
                counts["total"] = connection.scalar(select(func.count()).select_from(table))
                for field in ("type", "status"):
                    if field in table.c:
                        counts[f"by_{field}"] = dict(
                            connection.execute(
                                select(table.c[field], func.count()).group_by(table.c[field])
                            )
                            .tuples()
                            .all()
                        )
            result.update(
                size_bytes=path.stat().st_size,
                schema_version=values.get("schema_version"),
                embedding_model=values.get("embedding_model"),
                needs_rebuild=values.get("schema_version") != SCHEMA_VERSION,
                entries=counts,
                last_run=json.loads(values["last_run"]) if values.get("last_run") else None,
            )
    finally:
        engine.dispose()
    return result
