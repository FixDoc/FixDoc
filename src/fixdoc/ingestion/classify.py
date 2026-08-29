"""Memory-type classification: is a resolution a fix, a playbook, or an insight?

Trimmed resurrection: the worthiness half (memory-worthy vs self-explanatory
error codes) left with the log lane; git history keeps it for the day live
error capture returns.
"""

import re

MEMORY_TYPES = {"fix", "check", "playbook", "insight"}

_PLAYBOOK_NUMBERED_RE = re.compile(r"^\s*\d+[\.\)]\s", re.MULTILINE)
_PLAYBOOK_BULLET_RE = re.compile(r"^\s*[-*]\s", re.MULTILINE)
_PLAYBOOK_STEP_RE = re.compile(r"^\s*(?:step|phase)\s+\d+", re.MULTILINE | re.IGNORECASE)
_PLAYBOOK_SEQUENCE_RE = re.compile(
    r"\b(?:then|after that|next|finally|afterwards|followed by|lastly|subsequently)\b",
    re.IGNORECASE,
)
_PLAYBOOK_ACTION_CHAIN_RE = re.compile(
    r"(?:^|[.;,])\s*(?:updated|changed|added|removed|restarted|applied|configured|"
    r"enabled|disabled|created|deleted|set|ran|deployed|patched|migrated|scaled)\b",
    re.IGNORECASE,
)
_PLAYBOOK_MIN_STEPS = 3

_CHECK_START_PATTERNS = (
    "verify",
    "confirm",
    "ensure",
    "check",
    "make sure",
    "validate",
    "assert",
    "verified",
    "confirmed",
    "ensured",
    "checked",
    "validated",  # past tense
    "tested",
    "test that",
    "run",
    "ran",  # testing
)
_CHECK_SHORT_THRESHOLD = 120

_CHECK_CONTAINS_PATTERNS = (
    "confirmed that",
    "verified that",
    "tested and confirmed",
    "make sure that",
    "ensure that",
    "validate that",
    "confirmed it",
    "verified it",
)
_CHECK_CONTAINS_THRESHOLD = 200

_INSIGHT_PHRASES = (
    "root cause",
    "the reason",
    "this happens when",
    "this occurs when",
    "the issue was that",
    "turns out",
    "lesson learned",
    "important to know",
    "key takeaway",
    "note that",
    "be aware",
    "caused by",
    "the problem was",
    "this is due to",
    "underlying issue",
    "the underlying issue",
    "this was caused by",
    "contributing factor",
    "after investigation",
    "upon review",
    "analysis showed",
    "the failure was due to",
    "traced back to",
)

_ACTIONABLE_VERBS = (
    "add",
    "remove",
    "change",
    "update",
    "set",
    "create",
    "delete",
    "modify",
    "replace",
    "configure",
    "enable",
    "disable",
)


def classify_memory_type(resolution: str) -> str:
    """Classify a fix resolution into a memory type.

    Priority order:
    1. Playbook (structure): 3+ numbered/bullet/step lines
    2. Check (keyword at start): starts with verify/ensure/etc.
    3. Insight (explanatory): contains insight phrases, no actionable verb first
    4. Fix (default)
    """
    if not resolution:
        return "fix"

    # 1. Playbook — structure-first + prose sequence detection
    step_count = (
        len(_PLAYBOOK_NUMBERED_RE.findall(resolution))
        + len(_PLAYBOOK_BULLET_RE.findall(resolution))
        + len(_PLAYBOOK_STEP_RE.findall(resolution))
        + len(_PLAYBOOK_SEQUENCE_RE.findall(resolution))
        + len(_PLAYBOOK_ACTION_CHAIN_RE.findall(resolution))
    )
    if step_count >= _PLAYBOOK_MIN_STEPS:
        return "playbook"

    # 2. Check — keyword at start
    stripped = resolution.strip().lower()
    if any(stripped.startswith(kw) for kw in _CHECK_START_PATTERNS):
        return "check"
    # Also: short text with check keyword anywhere
    if len(resolution) < _CHECK_SHORT_THRESHOLD:
        if any(kw in stripped for kw in _CHECK_START_PATTERNS):
            return "check"
    # Also: contains check phrase in mid-text (up to 200 chars)
    if len(resolution) < _CHECK_CONTAINS_THRESHOLD:
        lower_check = resolution.lower()
        if any(phrase in lower_check for phrase in _CHECK_CONTAINS_PATTERNS):
            return "check"

    # 3. Insight — explanatory phrases, guarded by actionable verb check
    lower = resolution.lower()
    if any(phrase in lower for phrase in _INSIGHT_PHRASES):
        first_word = resolution.strip().split()[0].lower() if resolution.strip() else ""
        if first_word not in _ACTIONABLE_VERBS:
            return "insight"

    # 4. Fix (default)
    return "fix"
