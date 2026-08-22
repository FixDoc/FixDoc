"""Append-only events log: what happened, when, with what payload.

One JSONL file in the index directory. It stays on the machine: never
committed, never phoned home. Rebuilding the index drops the SQLite file,
never this one — events are primary data, not derived state.

Why it exists from day one: every `retrieval -> confirm` pair in this log is
a labeled example of a fix that actually helped, and every search that came
back empty is a documented coverage gap. That data trains future ranking and
fills the eval harness — but only if it was being collected all along.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

EVENTS_FILE = "events.jsonl"


def log_event(index_dir, event_type, payload):
    """Append one event. Never rewrites, never reorders: append is the only op."""
    path = Path(index_dir) / EVENTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(timezone.utc).isoformat(), "type": event_type, "payload": payload}
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def read_events(index_dir):
    """All events in write order. A line truncated by a crash is skipped, not fatal —
    losing one event beats refusing to read the other ten thousand."""
    path = Path(index_dir) / EVENTS_FILE
    if not path.exists():
        return []
    events = []
    for line in path.read_text().splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events
