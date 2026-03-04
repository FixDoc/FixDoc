"""ServiceNow importer — CSV only (v1)."""

from typing import Optional

from .base import (
    build_fix,
    clean_text,
    detect_resource_types,
    normalize_tags,
)

_CLOSED_STATES = {"closed", "resolved", "7", "6"}


def _get(row: dict, *keys: str) -> str:
    """Try multiple header spellings (already lowercased), return first non-empty hit.
    Also tries underscore variant so JSON keys (e.g. close_notes) are found when
    caller passes the space-separated form (e.g. 'close notes').
    """
    for k in keys:
        v = row.get(k.lower(), "") or row.get(k.lower().replace(" ", "_"), "")
        if v:
            return v
    return ""


def is_closed(row: dict) -> bool:
    """True if state is Closed/Resolved/7/6."""
    return _get(row, "state", "incident state").strip().lower() in _CLOSED_STATES


def best_resolution(row: dict, allow_description: bool = False) -> Optional[str]:
    """
    Priority: Close notes → Resolution notes → Work notes → (optionally) Description.
    Returns None if all candidates are empty → row should be skipped.
    """
    candidates = [
        _get(row, "close notes"),
        _get(row, "resolution notes"),
        _get(row, "work notes"),
    ]
    if allow_description:
        candidates.append(_get(row, "description"))

    for candidate in candidates:
        cleaned = clean_text(candidate)
        if cleaned:
            return cleaned
    return None


def extract(
    rows: list,
    closed_only: bool,
    extra_tags: list,
    max_count: Optional[int],
    allow_description: bool = False,
) -> tuple:
    """
    Extract Fix objects from rows. Returns (fixes: list[Fix], bad_rows: int).
    Applies closed_only filter and --max cap (rows processed, not imported).
    """
    fixes = []
    bad_rows = 0
    processed = 0

    for row in rows:
        if max_count is not None and processed >= max_count:
            break
        processed += 1

        try:
            issue = clean_text(_get(row, "short description"))
            if not issue:
                bad_rows += 1
                continue

            if closed_only and not is_closed(row):
                continue

            resolution = best_resolution(row, allow_description=allow_description)
            if not resolution:
                bad_rows += 1
                continue

            error_excerpt = clean_text(_get(row, "description"))

            meta_parts = []
            for key in ["close code", "assignment group", "category", "subcategory"]:
                val = _get(row, key)
                if val:
                    meta_parts.append(f"{key}: {val.strip()}")
            meta_note = " | ".join(meta_parts) if meta_parts else ""

            source_id = _get(row, "number")
            source_tag = f"source:servicenow:{source_id}" if source_id else "source:servicenow:unknown"

            combined = issue + " " + resolution + " " + (error_excerpt or "")
            resource_types, kw_tags = detect_resource_types(combined)

            tags_str = normalize_tags(
                resource_types,
                kw_tags,
                source_tag,
                [t.lower() for t in extra_tags],
            )

            notes_str = f"Source: servicenow {source_id}" if source_id else "Source: servicenow"
            if meta_note:
                notes_str += f" | {meta_note}"

            fix = build_fix(
                issue,
                resolution,
                error_excerpt or None,
                tags_str,
                notes_str,
            )
            fixes.append(fix)

        except Exception:
            bad_rows += 1

    return fixes, bad_rows
