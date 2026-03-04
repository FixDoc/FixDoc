"""Jira importer — CSV and JSON (backup format)."""

import re
from typing import Optional

from .base import (
    build_fix,
    clean_text,
    detect_resource_types,
    normalize_tags,
    slugify_tag,
)

_CLOSED_STATUSES = {"done", "closed", "resolved"}
_COMMENT_SEP_RE = re.compile(r"\n-{5,}\n")


def _get(row: dict, *keys: str) -> str:
    """Try multiple header spellings (already lowercased), return first non-empty hit."""
    for k in keys:
        v = row.get(k.lower(), "")
        if v:
            return v
    return ""


def _parse_comment(raw: str) -> str:
    """If multi-segment (split on -----), return last non-empty segment."""
    segments = _COMMENT_SEP_RE.split(raw)
    segments = [s.strip() for s in segments if s.strip()]
    if len(segments) > 1:
        return segments[-1]
    return raw.strip()


def is_closed(row: dict) -> bool:
    """True if status is Done/Closed/Resolved (excludes Won't Do)."""
    status = (
        row.get("status", "")
        or row.get("status category", "")
    ).strip().lower()
    return status in _CLOSED_STATUSES


def _csv_row_to_fields(row: dict) -> Optional[dict]:
    """Map a normalised CSV row to common field dict. Returns None if unsalvageable."""
    issue = clean_text(_get(row, "summary"))
    if not issue:
        return None

    resolution_raw = _get(row, "resolution")
    comment_raw = _get(row, "comment")

    if resolution_raw:
        resolution = clean_text(resolution_raw)
    elif comment_raw:
        resolution = clean_text(_parse_comment(comment_raw))
    else:
        return None

    if not resolution:
        return None

    desc_raw = _get(row, "description")
    error_excerpt = clean_text(desc_raw.split("\n\n")[0].strip()) if desc_raw else ""

    tag_parts = []
    for raw in [_get(row, "labels"), _get(row, "components")]:
        for part in raw.replace(";", ",").split(","):
            s = slugify_tag(part.strip())
            if s:
                tag_parts.append(s)

    return {
        "issue": issue,
        "resolution": resolution,
        "error_excerpt": error_excerpt,
        "tag_parts": tag_parts,
        "source_id": _get(row, "issue key", "key"),
        "status": _get(row, "status", "status category"),
    }


def _json_row_to_fields(issue: dict) -> Optional[dict]:
    """Map a Jira backup JSON issue to common field dict. Returns None if unsalvageable."""
    fields = issue.get("fields", {})

    summary = str(fields.get("summary") or "").strip()
    if not summary:
        return None

    resolution_obj = fields.get("resolution") or {}
    resolution = str(resolution_obj.get("name") or "").strip() if isinstance(resolution_obj, dict) else ""

    if not resolution:
        comment_obj = fields.get("comment") or {}
        comments = comment_obj.get("comments", []) if isinstance(comment_obj, dict) else []
        if comments and isinstance(comments[-1], dict):
            resolution = clean_text(str(comments[-1].get("body") or "").strip())

    if not resolution:
        return None

    desc_raw = str(fields.get("description") or "").strip()
    error_excerpt = clean_text(desc_raw.split("\n\n")[0].strip()) if desc_raw else ""

    tag_parts = []
    for lbl in fields.get("labels", []) or []:
        s = slugify_tag(str(lbl).strip())
        if s:
            tag_parts.append(s)
    for comp in fields.get("components", []) or []:
        name = comp.get("name", "") if isinstance(comp, dict) else str(comp)
        s = slugify_tag(name.strip())
        if s:
            tag_parts.append(s)

    status_obj = fields.get("status") or {}
    status = str(status_obj.get("name") or "") if isinstance(status_obj, dict) else ""

    return {
        "issue": clean_text(summary),
        "resolution": resolution,
        "error_excerpt": error_excerpt,
        "tag_parts": tag_parts,
        "source_id": str(issue.get("key") or "").strip(),
        "status": status,
    }


def extract(
    rows: list,
    closed_only: bool,
    extra_tags: list,
    max_count: Optional[int],
    is_json: bool = False,
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
            fields = _json_row_to_fields(row) if is_json else _csv_row_to_fields(row)
        except Exception:
            bad_rows += 1
            continue

        if fields is None:
            bad_rows += 1
            continue

        if closed_only and fields["status"].strip().lower() not in _CLOSED_STATUSES:
            continue

        source_id = fields["source_id"]
        source_tag = f"source:jira:{source_id}" if source_id else "source:jira:unknown"
        combined = fields["issue"] + " " + fields["resolution"] + " " + fields["error_excerpt"]
        resource_types, kw_tags = detect_resource_types(combined)

        tags_str = normalize_tags(
            resource_types,
            kw_tags,
            source_tag,
            fields["tag_parts"] + [t.lower() for t in extra_tags],
        )

        fix = build_fix(
            fields["issue"],
            fields["resolution"],
            fields["error_excerpt"],
            tags_str,
            f"Source: jira {source_id}" if source_id else "Source: jira",
        )
        fixes.append(fix)

    return fixes, bad_rows
