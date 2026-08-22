"""Incremental SQLite index over a knowledge store directory.

Derived state, always rebuildable from the markdown. Content-hash per file
so unchanged entries are never re-embedded; embeddings are versioned by
model name so a model change wholesale-invalidates the index.
"""

import hashlib
import sqlite3
from array import array
from collections import namedtuple
from pathlib import Path

from .models import Entry

# ponytail: brute-force cosine over all rows; add usearch/hnswlib if stores
# grow past ~50k entries and search latency actually hurts.

Candidate = namedtuple("Candidate", "id type resource_type")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    resource_type TEXT,
    title TEXT,
    occurrences INTEGER,
    confidence REAL,
    embedding BLOB
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class Index:
    def __init__(self, index_dir, embed_fn, model_name):
        self.index_dir = Path(index_dir)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.embed_fn = embed_fn
        self.model_name = model_name
        self.db = sqlite3.connect(str(self.index_dir / "index.db"))
        self.db.executescript(_SCHEMA)
        row = self.db.execute(
            "SELECT value FROM meta WHERE key = 'embedding_model'"
        ).fetchone()
        if row and row[0] != model_name:
            self.db.execute("DELETE FROM entries")
        self.db.execute(
            "INSERT OR REPLACE INTO meta VALUES ('embedding_model', ?)", (model_name,)
        )
        self.db.commit()

    def sync(self, store_dir):
        """Bring the index up to date with knowledge/. Returns stats dict."""
        store_dir = Path(store_dir)
        stats = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0, "skipped": []}
        known = dict(self.db.execute("SELECT path, content_hash FROM entries"))
        seen = set()
        if store_dir.is_dir():
            for path in sorted(store_dir.rglob("*.md")):
                rel = str(path.relative_to(store_dir))
                seen.add(rel)
                text = path.read_text()
                content_hash = hashlib.sha256(text.encode()).hexdigest()
                if known.get(rel) == content_hash:
                    stats["unchanged"] += 1
                    continue
                try:
                    entry = Entry.from_markdown(text)
                except (ValueError, KeyError):
                    stats["skipped"].append(rel)
                    seen.discard(rel)
                    continue
                vector = array("f", self.embed_fn(entry.search_text()))
                self.db.execute(
                    "INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (entry.id, rel, content_hash, entry.type, entry.status,
                     entry.resource_type, entry.title, entry.occurrences,
                     entry.confidence, vector.tobytes()),
                )
                stats["updated" if rel in known else "added"] += 1
        for rel in set(known) - seen:
            self.db.execute("DELETE FROM entries WHERE path = ?", (rel,))
            stats["removed"] += 1
        self.db.commit()
        return stats

    def candidates(self, entry_type):
        """(Candidate, vector) pairs for dedup: live entries of one type."""
        rows = self.db.execute(
            "SELECT id, type, resource_type, embedding FROM entries "
            "WHERE type = ? AND status NOT IN ('deprecated', 'rejected')",
            (entry_type,),
        ).fetchall()
        return [
            (Candidate(r[0], r[1], r[2]), list(array("f", r[3]))) for r in rows
        ]
