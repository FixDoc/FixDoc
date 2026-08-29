"""Secret redaction: runs on every extracted string BEFORE anything touches disk.

The store is a git repo that gets pushed, so a secret that reaches a knowledge
file is a secret published to the team remote. Replacements are typed
([REDACTED:aws-key]) so entries stay readable and reviewers can see what kind
of value was removed. False positives are acceptable; leaked secrets are not.
"""

import re

# Order matters: structured tokens (keys, JWTs, PEM blocks) are matched before
# the generic name=value credential pattern so they get their specific label.
_PATTERNS = [
    (
        "private-key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
        ),
    ),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/-]{12,}=*")),
]
_URL_CRED = re.compile(r"://([^/\s:@]+):([^/\s@]+)@")
_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key(?:[_-]?id)?"
    r"|client[_-]?secret)\b(\s*[=:]\s*)(\"[^\"]+\"|'[^']+'|\S+)"
)


def redact(text):
    """Returns (clean_text, counts_by_pattern_name). Empty counts = untouched."""
    counts = {}

    def bump(name, n):
        if n:
            counts[name] = counts.get(name, 0) + n

    for name, pattern in _PATTERNS:
        text, n = pattern.subn(f"[REDACTED:{name}]", text)
        bump(name, n)
    text, n = _URL_CRED.subn("://[REDACTED:url-credential]@", text)
    bump("url-credential", n)
    # Keep the variable name (reviewers need to know WHICH credential), kill the value.
    text, n = _ASSIGNMENT.subn(r"\1\2[REDACTED:credential]", text)
    bump("credential", n)
    return text, counts
