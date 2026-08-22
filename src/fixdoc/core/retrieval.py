"""Two-stage retrieval: metadata filter, blended rank, token budget.

The hot path. Filtering does most of the precision work (wrong-universe
entries become non-candidates before any vector math); ranking blends
similarity with trust earned through the validated write path; the token
budget decides how much of each result the agent actually sees.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from .dedup import _cosine
from .models import Entry

# TODO(calibration): hand-tuned weights; the eval harness's golden
# retrieval set is what justifies changing them.
W_SIM = 1.0
W_KEYS = 0.3
W_TRUST = 0.3
W_AGE = 0.1


@dataclass
class Retrieved:
    id: str
    type: str
    title: str
    status: str
    occurrences: int
    confidence: Optional[float]
    similarity: float
    score: float
    detail: str  # "full" | "summary" | "title"
    content: str


def _tokens(text):
    # ponytail: ~4 chars/token heuristic; a real tokenizer is a dependency
    # for ~5% accuracy nobody needs yet.
    return len(text) // 4 + 1 if text else 0


def _staleness(created):
    try:
        age_days = (date.today() - date.fromisoformat(created)).days
    except (TypeError, ValueError):
        return 0.0
    return min(max(age_days, 0) / 365.0, 1.0)


def _key_overlap(query_keys, entry_keys):
    if not query_keys:
        return 0.0
    hits = sum(1 for k, v in query_keys.items() if entry_keys.get(k) == v)
    return hits / len(query_keys)


def _trust(occurrences, confidence):
    proven = min(occurrences or 0, 5) / 5
    judged = confidence if confidence is not None else 0.5
    return 0.5 * proven + 0.5 * judged


def search(index, store_dir, query, *, entry_type=None, resource_type=None,
           env=None, match_keys=None, token_budget=2000, limit=5,
           include_quarantined=False):
    """Ranked, token-budgeted results. Bodies come whole or not at all."""
    query_vector = index.embed_fn(query)
    scored = []
    for row in index.live(entry_type=entry_type,
                          include_quarantined=include_quarantined):
        if resource_type and row.resource_type and row.resource_type != resource_type:
            continue
        if env and row.env_scope and env not in row.env_scope:
            continue
        similarity = _cosine(query_vector, row.vector)
        score = (
            W_SIM * similarity
            + W_KEYS * _key_overlap(match_keys or {}, row.match_keys)
            + W_TRUST * _trust(row.occurrences, row.confidence)
            - W_AGE * _staleness(row.created)
        )
        scored.append((score, similarity, row))
    scored.sort(key=lambda item: item[0], reverse=True)

    results = []
    remaining = token_budget
    for rank, (score, similarity, row) in enumerate(scored[:limit]):
        entry = Entry.from_markdown((Path(store_dir) / row.path).read_text())
        full = entry.title + "\n\n" + "\n\n".join(
            f"## {name}\n\n{text}" for name, text in entry.sections.items()
        )
        summary = entry.title + "\n\n" + entry.return_text()
        levels = [("full", full), ("summary", summary)] if rank == 0 else [("summary", summary)]
        detail, content = "title", ""  # beyond-budget results still list title+id
        for level, text in levels:
            if _tokens(text) <= remaining:
                detail, content = level, text
                break
        remaining -= _tokens(content)
        results.append(Retrieved(row.id, row.type, row.title, row.status,
                                 row.occurrences, row.confidence, similarity,
                                 score, detail, content))
    return results
