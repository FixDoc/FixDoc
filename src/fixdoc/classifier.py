"""Memory-worthiness classifier for pending errors.

Classifies each PendingEntry as "memory_worthy" or "self_explanatory".
Self-explanatory errors (e.g. missing required argument) are still stored
but hidden from the default UX.

Also classifies fix resolutions into memory types: fix, check, playbook, insight.
"""

import re
from typing import Optional

RECURRENCE_PROMOTION_THRESHOLD = 3

_SELF_EXPLANATORY_CODES = {
    # TF config errors (from TF_CONFIG_ERRORS values)
    "InvalidDefaultValue",
    "MissingRequiredVariable",
    "UnsupportedArgument",
    "MissingRequiredArgument",
    "UndeclaredReference",
    "InvalidInputVariable",
    "UndeclaredAttribute",
    # TF init errors (from _INIT_CODES)
    "InconsistentLockFile",
    "ModuleNotInstalled",
    "ProviderQueryFailed",
    # AWS validation
    "ValidationException",
    "ValidationError",
    "InvalidParameterValue",
    "InvalidParameterCombination",
    "InvalidAMIID.Malformed",
    # Azure validation
    "InvalidParameter",
    "BadRequest",
    # GCP validation
    "InvalidArgument",
    # K8s config/validation
    "Invalid",
    # Helm
    "ChartNotFound",
    "NoDeployedReleases",
}

_MEMORY_WORTHY_CODES = {
    # Auth/authz
    "AccessDenied",
    "AccessDeniedException",
    "UnauthorizedAccess",
    "AuthorizationFailed",
    "AuthenticationFailed",
    "RBACDenied",
    # Resource conflict/drift
    "BucketAlreadyExists",
    "BucketAlreadyOwnedByYou",
    "StorageAccountAlreadyTaken",
    "ConflictError",
    "Conflict",
    "RoleAssignmentExists",
    "ResourceInUseException",
    "ReleaseExists",
    # Infrastructure/networking
    "InvalidSubnet",
    "InvalidVpcID",
    "DBSubnetGroupDoesNotCoverEnoughAZs",
    "PrincipalNotFound",
    # Capacity/quota
    "LimitExceeded",
    "QuotaExceeded",
    "ServiceQuotaExceededException",
    "SkuNotAvailable",
    "InsufficientInstanceCapacity",
    "InstanceLimitExceeded",
    "StorageQuotaExceeded",
    # K8s critical
    "CrashLoopBackOff",
    "OOMKilled",
    # K8s operational
    "ImagePullBackOff",
    "ErrImagePull",
    "FailedScheduling",
    "Timeout",
    "HookFailed",
    # K8s auth
    "Forbidden",
}

# Message patterns that indicate self-explanatory validation errors.
# These fire when there is no known error code but the message itself
# tells the engineer exactly what is wrong (e.g. "not a valid CIDR block").
_SELF_EXPLANATORY_MESSAGE_PATTERNS = (
    "is not a valid cidr",
    "not a valid cidr",
    "invalid cidr",
    "invalid json",
    "contains an invalid json",
    "expected a json",
    "not a valid ip",
    "invalid ip address",
    "invalid arn",
    "expected type",
    "inappropriate value for attribute",
    "invalid value for",
    "must be a whole number",
    "must be between",
    "not a valid url",
    "expected to be a string",
    "expected to be a number",
    "expected to be a bool",
    "expected to be a list",
    "expected to be a map",
    "this value does not have any",
    # Existence / duplicate
    "already exists",
    "already been created",
    "duplicate",
    # Syntax / format
    "malformed",
    "syntax error",
    "parse error",
    "unexpected token",
    "invalid syntax",
    "unterminated string",
    "unexpected end",
    # Type / constraint
    "type mismatch",
    "cannot be empty",
    "is required",
    "must not be empty",
    "out of range",
    "too long",
    "too short",
    "exceeds the maximum",
    "below the minimum",
    # Reference
    "is not defined",
    "was not found",
    "does not exist",
    "no such file",
    "could not find",
    "unknown attribute",
    "unknown variable",
    "unknown resource",
)

# HTTP status code classification for parser-derived codes like "403Forbidden"
_HTTP_STATUS_RE = re.compile(r"^(\d{3})")

_SELF_EXPLANATORY_HTTP = {"400", "404", "422"}
_MEMORY_WORTHY_HTTP = {"401", "403", "409", "429", "500", "502", "503"}


def _classify_http_status_code(code: str) -> Optional[str]:
    """Classify HTTP-status-derived error codes like '403Forbidden'."""
    m = _HTTP_STATUS_RE.match(code)
    if not m:
        return None
    status = m.group(1)
    if status in _MEMORY_WORTHY_HTTP:
        return "memory_worthy"
    if status in _SELF_EXPLANATORY_HTTP:
        return "self_explanatory"
    return None


# Cloud resource type prefixes
_RESOURCE_PREFIXES = ("aws_", "azurerm_", "google_", "kubernetes_")


def _resource_type_from_address(address: Optional[str]) -> Optional[str]:
    """Extract cloud resource type from a resource address.

    Examples:
        'aws_iam_role.app' -> 'aws_iam_role'
        'module.app.aws_s3_bucket.data' -> 'aws_s3_bucket'
        'variable.foo' -> 'variable'
        None -> None
    """
    if not address:
        return None
    # Scan for cloud resource type prefixed part
    for part in address.split("."):
        if any(part.startswith(prefix) for prefix in _RESOURCE_PREFIXES):
            return part
    # Fall back to first dotted segment
    return address.split(".")[0]


def count_similar_recurrences(entry, store) -> int:
    """Count similar entries in the store using normalized matching.

    Primary: same error_code AND same resource type prefix.
    Fallback (no error_code): same kind + first token of short_message.
    Excludes the entry itself by error_id.
    """
    all_entries = store.list_all(include_superseded=True, include_self_explanatory=True)
    count = 0
    entry_rt = _resource_type_from_address(entry.resource_address)

    for other in all_entries:
        if other.error_id == entry.error_id:
            continue

        if entry.error_code:
            other_rt = _resource_type_from_address(other.resource_address)
            if other.error_code == entry.error_code and other_rt == entry_rt:
                count += 1
        else:
            # Fallback: same kind + first token of short_message
            if other.kind == entry.kind:
                entry_first = (entry.short_message or "").split()[0] if entry.short_message else ""
                other_first = (other.short_message or "").split()[0] if other.short_message else ""
                if entry_first and entry_first == other_first:
                    count += 1

    return count


def _is_self_explanatory_message(message: Optional[str]) -> bool:
    """Check if the error message is self-explanatory based on patterns.

    Catches validation errors like 'not a valid CIDR block', 'invalid JSON',
    etc. where the message itself tells the engineer exactly what's wrong.
    """
    if not message:
        return False
    lower = message.lower()
    return any(pattern in lower for pattern in _SELF_EXPLANATORY_MESSAGE_PATTERNS)


def classify_entry(entry, store=None) -> str:
    """Classify a PendingEntry as 'memory_worthy' or 'self_explanatory'.

    Classification order:
    1. Recurrence (if store): >= RECURRENCE_PROMOTION_THRESHOLD -> memory_worthy
    2. Kind override: terraform_config or terraform_init -> self_explanatory
    3. Error code lookup: code in sets
    3.5. Message heuristic: self-explanatory validation messages
    4. Default: resource kind -> memory_worthy; else -> self_explanatory
    """
    # 1. Recurrence check
    if store is not None:
        if count_similar_recurrences(entry, store) >= RECURRENCE_PROMOTION_THRESHOLD:
            return "memory_worthy"

    # 2. Kind override
    if entry.kind in ("terraform_config", "terraform_init"):
        return "self_explanatory"

    # 3. Error code lookup
    if entry.error_code:
        if entry.error_code in _MEMORY_WORTHY_CODES:
            return "memory_worthy"
        if entry.error_code in _SELF_EXPLANATORY_CODES:
            return "self_explanatory"

    # 3b. HTTP status code pattern (e.g. "403Forbidden", "400BadRequest")
    if entry.error_code:
        http_result = _classify_http_status_code(entry.error_code)
        if http_result:
            return http_result

    # 3.5. Message heuristic — validation errors with self-explanatory messages
    if _is_self_explanatory_message(entry.short_message):
        return "self_explanatory"

    # 4. Default
    if entry.kind == "resource":
        return "memory_worthy"
    return "self_explanatory"


# ===================================================================
# Memory type classification (Phase 2)
# ===================================================================

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
