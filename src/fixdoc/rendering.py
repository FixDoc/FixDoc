"""Presentation/rendering helpers for fix suggestions.

Type-aware preview formatting for watch suggestions and Slack notifications.
"""

import re

_LIST_MARKER_RE = re.compile(r"^\s*(?:\d+[\.\)]\s*|[-*]\s*)")

_CHECK_KEYWORDS = (
    "verify",
    "confirm",
    "ensure",
    "check",
    "make sure",
    "validate",
    "assert",
)


def _strip_check_prefix(text):
    """Remove leading check keywords to avoid 'Verify: Verify that...' stutter."""
    lower = text.lstrip().lower()
    for kw in _CHECK_KEYWORDS:
        if lower.startswith(kw):
            stripped = text.lstrip()[len(kw) :].lstrip(" :").lstrip()
            return stripped if stripped else text
    return text


def _extract_first_step(resolution):
    """Extract the text of the first step, stripping list markers."""
    for line in resolution.strip().splitlines():
        stripped = line.strip()
        if re.match(r"^\s*\d+[\.\)]\s", stripped) or re.match(r"^\s*[-*]\s", stripped):
            return _LIST_MARKER_RE.sub("", stripped).strip()
    return resolution.split("\n")[0].strip()


def _count_steps(resolution):
    """Count step-like lines in resolution text."""
    count = 0
    for line in resolution.strip().splitlines():
        stripped = line.strip()
        if re.match(r"^\s*\d+[\.\)]\s", stripped) or re.match(r"^\s*[-*]\s", stripped):
            count += 1
    return count


def _truncate(text, max_len):
    """Truncate text to max_len, adding '...' if needed."""
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def format_suggestion_preview(fix, max_len=60):
    """Type-aware preview formatting for fix suggestions.

    Rendering rules by memory_type:
    - fix: plain resolution truncated at max_len (backward compatible)
    - check: "Verify: {resolution}" with stutter prevention, max_len=80
    - playbook: "Playbook (N steps): {first step}"
    - insight: "Context: {resolution}"
    """
    memory_type = getattr(fix, "memory_type", "fix") or "fix"
    resolution = fix.resolution or ""

    if memory_type == "check":
        effective_max = max(max_len, 80)
        cleaned = _strip_check_prefix(resolution)
        preview = f"Verify: {cleaned}"
        return _truncate(preview, effective_max)

    if memory_type == "playbook":
        steps = _count_steps(resolution)
        first = _extract_first_step(resolution)
        preview = f"Playbook ({steps} steps): {first}"
        return _truncate(preview, max_len)

    if memory_type == "insight":
        preview = f"Context: {resolution}"
        return _truncate(preview, max_len)

    # fix (default) — backward compatible
    return _truncate(resolution, max_len)
