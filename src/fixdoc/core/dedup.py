"""Three-band dedup gate: duplicate / related / distinct.

Pure decision logic — the caller (record pipeline) acts on the band:
duplicate -> no new file, occurrences++ on the match; related -> quarantine
with a ``related:`` link; distinct -> normal quarantine.
"""

from dataclasses import dataclass
from math import sqrt
from typing import Callable, Iterable, Optional, Sequence, Tuple

from .models import Entry

DUPLICATE_THRESHOLD = 0.92
RELATED_THRESHOLD = 0.75


@dataclass
class DedupDecision:
    band: str  # "duplicate" | "related" | "distinct"
    matched_id: Optional[str]  # best match even when distinct, for the events log
    similarity: float


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = sqrt(sum(x * x for x in a)) * sqrt(sum(x * x for x in b))
    return dot / norm if norm else 0.0


def dedup_check(
    entry: Entry,
    candidates: Iterable[Tuple[Entry, Sequence[float]]],
    embed_fn: Callable[[str], Sequence[float]],
    duplicate_threshold: float = DUPLICATE_THRESHOLD,
    related_threshold: float = RELATED_THRESHOLD,
) -> DedupDecision:
    """Compare a new entry against (candidate, embedding) pairs from the index.

    Scope: same type only; same resource_type when both sides declare one.
    """
    vector = embed_fn(entry.search_text())
    best_id, best_sim = None, 0.0
    for candidate, cand_vector in candidates:
        if candidate.type != entry.type:
            continue
        if (
            entry.resource_type
            and candidate.resource_type
            and candidate.resource_type != entry.resource_type
        ):
            continue
        sim = _cosine(vector, cand_vector)
        if best_id is None or sim > best_sim:
            best_id, best_sim = candidate.id, sim
    if best_id is None:
        return DedupDecision("distinct", None, 0.0)
    if best_sim >= duplicate_threshold:
        band = "duplicate"
    elif best_sim >= related_threshold:
        band = "related"
    else:
        band = "distinct"
    return DedupDecision(band, best_id, best_sim)
