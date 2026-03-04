"""Base importer for fixdoc — shared utilities for Jira and ServiceNow importers."""

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..models import Fix


# ---------------------------------------------------------------------------
# ImportResult
# ---------------------------------------------------------------------------

@dataclass
class ImportResult:
    """Summary of an import run."""
    imported: int = 0
    skipped: int = 0
    duplicates: int = 0
    low_signal: int = 0
    bad_rows: int = 0
    dry_run: bool = False
    tag_counts: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# CSV / JSON parsing
# ---------------------------------------------------------------------------

def parse_csv(path: Path) -> tuple:
    """
    Parse a CSV file. Returns (rows: list[dict], bad_rows: int).
    Handles UTF-8 BOM, auto-detects delimiter via csv.Sniffer.
    Normalises header names: strip + lowercase.
    """
    bad_rows = 0
    rows = []

    with open(path, encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(2048)
        fh.seek(0)

        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            delimiter = dialect.delimiter
        except csv.Error:
            print("[import] delimiter detection failed; defaulting to comma")
            delimiter = ","

        reader = csv.DictReader(fh, delimiter=delimiter)

        if reader.fieldnames:
            reader.fieldnames = [
                (h.strip().lower() if h else h) for h in reader.fieldnames
            ]

        for raw in reader:
            try:
                row = {
                    (k.strip().lower() if k else k): (v.strip() if v else "")
                    for k, v in raw.items()
                }
                rows.append(row)
            except Exception:
                bad_rows += 1

    return rows, bad_rows


def parse_json(path: Path) -> list:
    """
    Parse a JSON file (Jira backup format or bare array). Returns list of dicts.
    Handles both {"issues": [...]} wrapper and bare [...] array.
    """
    with open(path, encoding="utf-8-sig") as fh:
        data = json.load(fh)

    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "issues" in data:
        return data["issues"]
    if isinstance(data, dict) and "records" in data:
        return data["records"]
    return [data]


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

_HTML_SAFE_TAGS = {"p", "br", "div", "span", "b", "i", "em", "strong", "li", "ul", "ol", "a"}
_HTML_HINT = re.compile(r"<(p|br|div|span|a)\b|</", re.IGNORECASE)
_STRIP_TAG_RE = re.compile(
    r"</?(?:" + "|".join(_HTML_SAFE_TAGS) + r")\b[^>]*>",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"[ \t]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """Strip safe HTML tags if HTML is detected; otherwise collapse whitespace."""
    if not text:
        return text
    if _HTML_HINT.search(text):
        text = _STRIP_TAG_RE.sub(" ", text)
    lines = text.split("\n")
    lines = [_WHITESPACE_RE.sub(" ", ln).strip() for ln in lines]
    text = "\n".join(lines)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def slugify_tag(text: str) -> str:
    """Lowercase, spaces/hyphens → underscore, strip non-alphanumeric-or-underscore."""
    text = text.lower()
    text = re.sub(r"[ \-]+", "_", text)
    text = re.sub(r"[^\w]", "", text)
    return text.strip("_")


def normalize_tags(
    resource_types: list,
    keywords: list,
    source_tag: str,
    user_tags: list,
) -> str:
    """
    Stable-sort and deduplicate tags:
    resource_types (sorted) → kw: tags (sorted) → source: tag → user --tags (lowercased).
    Returns comma-separated string (no spaces).
    """
    seen = set()
    result = []

    def _add(tag: str) -> None:
        t = tag.strip().lower()
        if t and t not in seen:
            seen.add(t)
            result.append(t)

    for rt in sorted(set(resource_types)):
        _add(rt)
    for kw in sorted(set(keywords)):
        _add(kw)
    _add(source_tag)
    for ut in user_tags:
        _add(ut)

    return ",".join(result)


# ---------------------------------------------------------------------------
# Resource-type / keyword detection
# ---------------------------------------------------------------------------

_RESOURCE_TYPE_RE = re.compile(
    r"\b(aws_\w+|azurerm_\w+|google_\w+|kubernetes_\w+)\b"
)
_KEYWORDS = [
    "iam", "s3", "ec2", "terraform", "rbac", "alb", "rds",
    "lambda", "vpc", "security_group", "subnet", "bucket", "role",
]
_KW_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def detect_resource_types(text: str) -> tuple:
    """
    Returns (resource_types, kw_tags).
    Caller concatenates issue + ' ' + resolution + ' ' + error_excerpt before calling.
    """
    resource_types = list(dict.fromkeys(m.group(0) for m in _RESOURCE_TYPE_RE.finditer(text)))
    kw_matches = {m.group(1).lower() for m in _KW_PATTERN.finditer(text)}
    kw_tags = [f"kw:{k}" for k in sorted(kw_matches)]
    return resource_types, kw_tags


# ---------------------------------------------------------------------------
# Signal detection
# ---------------------------------------------------------------------------

_HIGH_SIGNAL_KW = {"kw:terraform", "kw:iam", "kw:rbac", "kw:kubernetes"}


def is_high_signal(fix: Fix) -> bool:
    """True if fix has at least one bare resource type tag OR a high-signal kw tag."""
    if not fix.tags:
        return False
    tags = {t.strip() for t in fix.tags.split(",") if t.strip()}
    for tag in tags:
        if re.match(r"^(aws_|azurerm_|google_|kubernetes_)\w+$", tag):
            return True
    return bool(tags & _HIGH_SIGNAL_KW)


# ---------------------------------------------------------------------------
# Fix builder
# ---------------------------------------------------------------------------

_ISSUE_MAX = 300
_RESOLUTION_MAX = 3000
_EXCERPT_MAX = 2000


def build_fix(
    issue: str,
    resolution: str,
    error_excerpt: Optional[str],
    tags: str,
    notes: Optional[str],
) -> Fix:
    """Build a Fix with silent field truncation."""
    issue = (issue or "").strip()[:_ISSUE_MAX]
    resolution = (resolution or "").strip()[:_RESOLUTION_MAX]
    if error_excerpt:
        error_excerpt = error_excerpt.strip()[:_EXCERPT_MAX]
    return Fix(
        issue=issue,
        resolution=resolution,
        error_excerpt=error_excerpt or None,
        tags=tags or None,
        notes=notes or None,
    )
