"""Notion importer — API-based, no extra runtime deps (uses urllib)."""

import json
import urllib.request
import urllib.error
from typing import Callable, List, Optional, Tuple

from .base import build_fix, clean_text, detect_resource_types, normalize_tags

_NOTION_API_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"

_DEFAULT_TITLE_FIELDS = [
    "name", "title", "summary", "incident", "issue", "problem"
]
_DEFAULT_RESOLUTION_FIELDS = [
    "resolution", "fix", "fix / mitigation", "postmortem",
    "remediation", "outcome", "action taken", "learnings",
]
_DEFAULT_STATUS_FIELDS = [
    "status", "state", "ticket status", "progress", "stage"
]
_DEFAULT_DONE_VALUES = {
    "done", "closed", "resolved", "fixed", "complete", "completed", "solved"
}


# ---------------------------------------------------------------------------
# Property text extraction
# ---------------------------------------------------------------------------

def _get_property_text(prop: dict) -> str:
    """Extract plain text from a Notion property value dict."""
    if not prop:
        return ""
    prop_type = prop.get("type", "")

    if prop_type in ("title", "rich_text"):
        items = prop.get(prop_type, [])
        return "".join(item.get("plain_text", "") for item in items)
    elif prop_type in ("select", "status"):
        val = prop.get(prop_type)
        if val and isinstance(val, dict):
            return val.get("name", "")
        return ""
    elif prop_type == "multi_select":
        items = prop.get("multi_select", [])
        return ", ".join(item.get("name", "") for item in items)
    elif prop_type == "url":
        return prop.get("url", "") or ""
    return ""


# ---------------------------------------------------------------------------
# Field matching
# ---------------------------------------------------------------------------

def _find_field(props: dict, candidates: List[str]) -> Tuple[Optional[str], Optional[dict]]:
    """
    Ranked matching against property keys:
    1. Exact normalized match (lowercase strip)
    2. Alias match (candidate is exact case-insensitive key)
    3. Partial containment (key contains the candidate string)

    Returns (key, value) or (None, None) if no match.
    """
    normalized_keys = {k.lower().strip(): k for k in props}

    # Pass 1: exact normalized match
    for candidate in candidates:
        cand_norm = candidate.lower().strip()
        if cand_norm in normalized_keys:
            orig_key = normalized_keys[cand_norm]
            return orig_key, props[orig_key]

    # Pass 2: partial containment (key contains candidate)
    for candidate in candidates:
        cand_norm = candidate.lower().strip()
        for norm_k, orig_k in normalized_keys.items():
            if cand_norm in norm_k:
                return orig_k, props[orig_k]

    return None, None


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

def _notion_request(url: str, token: str, method: str = "GET", body: Optional[dict] = None) -> dict:
    """Make a Notion API request. Raises RuntimeError on HTTP errors."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise RuntimeError(f"Notion API error {e.code}: {body_text}") from e


def fetch_pages(token: str, database_id: str, max_count: Optional[int] = None) -> List[dict]:
    """
    Paginate through POST /v1/databases/{database_id}/query.
    Returns list of raw page dicts. Raises RuntimeError on HTTP error.
    """
    pages = []
    cursor = None

    while True:
        payload: dict = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor

        url = f"{_NOTION_API_BASE}/databases/{database_id}/query"
        result = _notion_request(url, token, method="POST", body=payload)

        for page in result.get("results", []):
            pages.append(page)
            if max_count is not None and len(pages) >= max_count:
                return pages

        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")

    return pages


def fetch_page_blocks(token: str, page_id: str) -> List[dict]:
    """
    GET /v1/blocks/{page_id}/children (single page, no recursion).
    Returns list of block dicts. Returns [] on any error.
    """
    try:
        url = f"{_NOTION_API_BASE}/blocks/{page_id}/children"
        result = _notion_request(url, token, method="GET")
        return result.get("results", [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Block text extraction
# ---------------------------------------------------------------------------

_BLOCK_TEXT_TYPES = {
    "paragraph", "heading_1", "heading_2", "heading_3",
    "bulleted_list_item", "numbered_list_item", "to_do", "quote", "callout",
}

_HEADING_TYPES = {"heading_1", "heading_2", "heading_3"}

_RESOLUTION_SECTION_HEADINGS = [
    "fix", "mitigation", "resolution", "fix/mitigation",
    "root cause", "action taken", "remediation", "workaround",
    "solution", "steps taken", "corrective action",
]


def extract_block_text(blocks: List[dict]) -> str:
    """Extract plain text from shallow blocks. Returns newline-joined string."""
    lines = []
    for block in blocks:
        block_type = block.get("type", "")
        if block_type not in _BLOCK_TEXT_TYPES:
            continue
        rich_text = block.get(block_type, {}).get("rich_text", [])
        text = "".join(item.get("plain_text", "") for item in rich_text)
        if text.strip():
            lines.append(text.strip())
    return "\n".join(lines).strip()


def extract_section_text(blocks: List[dict], section_candidates: List[str]) -> str:
    """
    Extract text from the first section whose heading matches a candidate.

    Scans blocks for heading blocks (heading_1/2/3) matching a candidate
    (case-insensitive). Collects content blocks between the heading and the
    next heading (or end of blocks). Returns "" if no match found.
    """
    candidates_lower = [c.lower().strip() for c in section_candidates]

    # Find the first matching heading index
    start_idx = None
    for i, block in enumerate(blocks):
        block_type = block.get("type", "")
        if block_type not in _HEADING_TYPES:
            continue
        rich_text = block.get(block_type, {}).get("rich_text", [])
        heading_text = "".join(
            item.get("plain_text", "") for item in rich_text
        ).strip().lower()
        if heading_text in candidates_lower:
            start_idx = i
            break

    if start_idx is None:
        return ""

    # Collect content blocks until the next heading or end
    lines = []
    for block in blocks[start_idx + 1:]:
        block_type = block.get("type", "")
        if block_type in _HEADING_TYPES:
            break
        if block_type not in _BLOCK_TEXT_TYPES:
            continue
        rich_text = block.get(block_type, {}).get("rich_text", [])
        text = "".join(item.get("plain_text", "") for item in rich_text)
        if text.strip():
            lines.append(text.strip())

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

def extract(
    pages: List[dict],
    closed_only: bool,
    extra_tags: List[str],
    max_count: Optional[int],
    title_field: Optional[str] = None,
    resolution_field: Optional[str] = None,
    status_field: Optional[str] = None,
    done_values: Optional[str] = None,
    fetch_blocks_fn: Optional[Callable] = None,
) -> Tuple[List, int, int, int]:
    """
    Extract Fix objects from Notion pages.
    Returns (fixes, skipped_open, skipped_missing, bad_rows).

    fetch_blocks_fn(page_id) -> list[dict]: injected for testing; defaults to no-op.
    """
    title_candidates = [title_field] if title_field else _DEFAULT_TITLE_FIELDS
    resolution_candidates = [resolution_field] if resolution_field else _DEFAULT_RESOLUTION_FIELDS
    status_candidates = [status_field] if status_field else _DEFAULT_STATUS_FIELDS

    done_set = (
        {v.strip().lower() for v in done_values.split(",")}
        if done_values
        else _DEFAULT_DONE_VALUES
    )

    fixes = []
    skipped_open = 0
    skipped_missing = 0
    bad_rows = 0
    processed = 0

    for page in pages:
        if max_count is not None and processed >= max_count:
            break
        processed += 1

        try:
            props = page.get("properties", {})
            page_id = page.get("id", "")
            page_url = page.get("url", "")

            # --- Title ---
            _, title_prop = _find_field(props, title_candidates)
            title = clean_text(_get_property_text(title_prop or {}))
            if not title:
                # Fallback: find the property with type "title"
                for key, val in props.items():
                    if val.get("type") == "title":
                        title = clean_text(_get_property_text(val))
                        break
            if not title:
                skipped_missing += 1
                continue

            # --- Status filter ---
            _, status_prop = _find_field(props, status_candidates)
            status_val = _get_property_text(status_prop or {}).strip().lower()
            if closed_only and status_val not in done_set:
                skipped_open += 1
                continue

            # --- Resolution property ---
            _, res_prop = _find_field(props, resolution_candidates)
            resolution = clean_text(_get_property_text(res_prop or {}))

            # --- Body fallback if resolution is empty ---
            if not resolution and fetch_blocks_fn is not None:
                blocks = fetch_blocks_fn(page_id)
                resolution = extract_section_text(blocks, _RESOLUTION_SECTION_HEADINGS)
                if not resolution:
                    resolution = extract_block_text(blocks)

            if not resolution:
                skipped_missing += 1
                continue

            # --- Build fix ---
            page_id_clean = page_id.replace("-", "")
            source_tag = f"source:notion:{page_id_clean}"
            notes = f"Source: notion {page_id_clean}"
            if page_url:
                notes += f" | url: {page_url}"

            combined = title + " " + resolution
            resource_types, kw_tags = detect_resource_types(combined)

            tags_str = normalize_tags(
                resource_types,
                kw_tags,
                source_tag,
                [t.lower() for t in extra_tags],
            )

            fix = build_fix(
                issue=title,
                resolution=resolution,
                error_excerpt=title[:120] if title else None,
                tags=tags_str,
                notes=notes,
            )
            fixes.append(fix)

        except Exception:
            bad_rows += 1

    return fixes, skipped_open, skipped_missing, bad_rows
