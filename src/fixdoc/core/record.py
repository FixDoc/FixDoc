"""The write pipeline: what record_fix and confirm_fix do under the hood.

Flow for a new entry:

    validate -> force quarantine -> score confidence -> dedup -> act on the band

The security posture lives here, in the engine, so no transport layer has to
be trusted: callers cannot pick the id (prevents overwriting an existing
file), cannot set the status (everything an agent writes starts quarantined),
and cannot claim their own confidence score.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .dedup import dedup_check
from .events import log_event
from .models import Entry, new_id


@dataclass
class RecordResult:
    """What happened, in a shape the MCP layer can narrate back to the agent —
    e.g. "confirmed existing fx_7c2a91e4, now at 4 occurrences"."""

    action: str  # "created" | "created_related" | "confirmed_existing" | "invalid"
    entry_id: Optional[str] = None
    matched_id: Optional[str] = None
    similarity: float = 0.0
    problems: list = field(default_factory=list)


def _completeness_score(entry):
    # Rule-based stand-in for the LLM judge: it rewards substance, not
    # correctness. Structure is already guaranteed by validate(), so what
    # varies is how much the author actually wrote — 0.5 for being complete,
    # up to 1.0 at roughly a paragraph or two (~500 chars) of body.
    # The judge, when configured, will replace this with a real assessment.
    body = " ".join(entry.sections.values())
    return round(0.5 + 0.5 * min(len(body) / 500.0, 1.0), 2)


def _rewrite_with_bump(store_dir, index, entry_id):
    """occurrences++ on an existing entry's file. Metadata-only: the engine may
    touch counters, never content — content changes are human acts (PRs)."""
    rel = index.path_for(entry_id)
    if rel is None:
        return None
    path = Path(store_dir) / rel
    entry = Entry.from_markdown(path.read_text())
    entry.occurrences += 1
    path.write_text(entry.to_markdown())
    index.sync(store_dir)
    return entry.occurrences


def record_entry(store_dir, index, entry, namespace="shared"):
    """Run one entry through the write pipeline. Returns a RecordResult."""
    # Engine-assigned identity: a caller-picked id could collide with (and
    # silently overwrite) an existing file, so whatever the caller sent is
    # discarded before anything touches disk.
    entry.id = new_id(entry.type)  # raises ValueError on unknown type

    problems = entry.validate()
    if problems:
        # Returned, not raised: the MCP layer forwards these so the agent's
        # retry loop can fix its own call ("missing required section: Fix").
        return RecordResult(action="invalid", problems=problems)

    entry.status = "quarantined"  # the write path's one non-negotiable
    entry.confidence = _completeness_score(entry)

    decision = dedup_check(entry, index.candidates(entry.type), index.embed_fn)

    if decision.band == "duplicate":
        # A duplicate is a confirmation signal, not noise: the same problem
        # recurred and the same knowledge covers it. No new file — the match
        # gets credit, and the full incoming text is preserved in the event
        # so nothing the agent reported is lost.
        occurrences = _rewrite_with_bump(store_dir, index, decision.matched_id)
        log_event(
            index.index_dir,
            "entry_recorded",
            {
                "action": "confirmed_existing",
                "entry_id": decision.matched_id,
                "similarity": decision.similarity,
                "occurrences": occurrences,
                "incoming": entry.to_markdown(),
            },
        )
        return RecordResult(
            "confirmed_existing",
            entry_id=decision.matched_id,
            matched_id=decision.matched_id,
            similarity=decision.similarity,
        )

    if decision.band == "related":
        # Ambiguous band: create the entry but link it, so the human reviewer
        # sees both side by side and decides merge vs coexist.
        entry.related = [decision.matched_id]

    path = Path(store_dir) / namespace / f"{entry.id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(entry.to_markdown())
    index.sync(store_dir)

    action = "created_related" if decision.band == "related" else "created"
    log_event(
        index.index_dir,
        "entry_recorded",
        {
            "action": action,
            "entry_id": entry.id,
            "matched_id": decision.matched_id,
            "similarity": decision.similarity,
        },
    )
    return RecordResult(
        action, entry_id=entry.id, matched_id=decision.matched_id, similarity=decision.similarity
    )


def confirm_entry(store_dir, index, entry_id, note=None):
    """The engine op behind confirm_fix: a retrieved entry resolved a real
    incident, so its occurrence count — the trust signal retrieval ranks by —
    goes up. Returns the new count, or None if the id is unknown."""
    occurrences = _rewrite_with_bump(store_dir, index, entry_id)
    if occurrences is None:
        return None
    log_event(
        index.index_dir,
        "entry_confirmed",
        {"entry_id": entry_id, "occurrences": occurrences, "note": note},
    )
    return occurrences
