"""The day-0 ingestion pipeline: documents and logs in, honest output out.

Two source kinds, two honest outputs:
  documents (postmortems, runbooks) -> quarantined knowledge entries, with
    "(add during review)" placeholders for anything the source never stated;
  logs -> a symptom QUEUE, never entries. Logs contain symptoms, not fixes,
    and a pipeline that fabricates fixes from error output would fill the
    store with authoritative-looking garbage.

Every extracted string passes through redaction before it is written.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from fixdoc.core.models import Entry

from .classify import classify_entry, classify_memory_type
from .parsers.router import detect_and_parse
from .redact import redact

PLACEHOLDER = "(not captured in the source document — add during review)"

# Heading vocabularies for mapping document sections onto the entry format.
# Matching is case-insensitive and by containment, so "Root Cause Analysis"
# matches "root cause".
SYMPTOM_HEADINGS = [
    "symptom",
    "summary",
    "impact",
    "what happened",
    "description",
    "when to use",
    "problem",
]
ROOT_CAUSE_HEADINGS = ["root cause", "why it happened", "cause", "diagnosis"]
FIX_HEADINGS = [
    "fix",
    "mitigation",
    "resolution",
    "action taken",
    "remediation",
    "workaround",
    "solution",
    "steps taken",
    "corrective action",
    "steps",
]
VERIFICATION_HEADINGS = ["verification", "validation", "how we knew", "confirmation", "verify"]

MAX_QUEUE_ITEMS = 100
EXCERPT_CHARS = 240


@dataclass
class IngestReport:
    entries_written: int = 0
    candidates: int = 0
    dropped_by_cap: int = 0
    skipped_no_resolution: int = 0
    queue_items: int = 0
    queue_occurrences: int = 0
    noise_errors: int = 0
    redactions: dict = field(default_factory=dict)


def _sections_from_markdown(text):
    """Split a markdown document into (title, {heading: content}) by #/##/### lines."""
    title, current, sections = None, None, {}
    for line in text.splitlines():
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            if title is None and line.startswith("# "):
                title = heading
                current = "_intro"
                sections[current] = []
                continue
            current = heading.lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
        else:
            sections.setdefault("_intro", []).append(line)
    return title, {k: "\n".join(v).strip() for k, v in sections.items()}


def _pick(sections, vocabulary):
    """First section whose heading contains any vocabulary term (order = priority)."""
    for term in vocabulary:
        for heading, content in sections.items():
            if term in heading and content:
                return content
    return None


def _merge_redactions(report, counts):
    for name, n in counts.items():
        report.redactions[name] = report.redactions.get(name, 0) + n


def _doc_candidate(path, report):
    """One document -> one entry draft, or None if it holds no resolution."""
    text, counts = redact(path.read_text(errors="replace"))
    _merge_redactions(report, counts)
    title, sections = _sections_from_markdown(text)
    fix_text = _pick(sections, FIX_HEADINGS)
    if not fix_text:
        report.skipped_no_resolution += 1
        return None
    symptom = _pick(sections, SYMPTOM_HEADINGS) or sections.get("_intro") or PLACEHOLDER
    root_cause = _pick(sections, ROOT_CAUSE_HEADINGS) or PLACEHOLDER
    verification = _pick(sections, VERIFICATION_HEADINGS) or PLACEHOLDER
    title = title or path.stem.replace("-", " ").replace("_", " ")

    entry_type = classify_memory_type(fix_text)
    if entry_type == "playbook":
        entry_sections = {"When to use": symptom, "Steps": fix_text, "Verification": verification}
        prefix = "pb"
    elif entry_type == "insight":
        entry_sections = {"Context": f"{symptom}\n\n{fix_text}".strip()}
        prefix = "in"
    else:
        entry_sections = {
            "Symptom": symptom,
            "Root cause": root_cause,
            "Fix": fix_text,
            "Verification": verification,
        }
        prefix = "fx"
    entry_sections["Notes"] = f"Ingested from {path.name}"

    # Deterministic id from extracted content: re-running ingest over the same
    # input is a no-op, and the review flow drains a big corpus in waves.
    digest = hashlib.sha256(f"{title}\n{symptom}\n{fix_text}".encode()).hexdigest()[:8]
    entry = Entry(
        id=f"{prefix}_{digest}",
        type=entry_type if entry_type != "check" else "fix",
        title=title,
        sections=entry_sections,
        status="quarantined",
        created="",
    )
    # Substance score: real sections beat placeholders, longer bodies beat stubs.
    real = sum(1 for v in entry_sections.values() if v and PLACEHOLDER not in v)
    score = (real / max(len(entry_sections), 1), len(f"{symptom}{fix_text}"))
    return score, entry


class _Shim:
    """Duck-typed adapter: gives the resurrected classifier the attributes it
    reads (kind, error_code, short_message, ...) from a ParsedError."""

    def __init__(self, parsed):
        address = parsed.resource_address or ""
        if address == "terraform.init":
            self.kind = "terraform_init"
        elif address.split(".")[0] in ("variable", "output", "local", "module", "provider"):
            self.kind = "terraform_config"
        else:
            self.kind = "resource"
        self.error_code = parsed.error_code
        self.resource_address = parsed.resource_address
        self.short_message = (
            (parsed.error_message or "").splitlines()[0][:200] if parsed.error_message else ""
        )
        self.error_id = ""


def _log_candidates(path, queue, report):
    """Errors from a log -> queue items keyed by (code, address), counted."""
    text, counts = redact(path.read_text(errors="replace"))
    _merge_redactions(report, counts)
    for parsed in detect_and_parse(text):
        shim = _Shim(parsed)
        if classify_entry(shim) != "memory_worthy":
            report.noise_errors += 1  # typos and missing arguments are not knowledge
            continue
        key = (parsed.error_code or "unknown", parsed.resource_address or "")
        if key in queue:
            queue[key]["count"] += 1
        else:
            excerpt = " ".join((parsed.error_message or "").split())[:EXCERPT_CHARS]
            queue[key] = {"code": key[0], "address": key[1], "count": 1, "excerpt": excerpt}


def ingest_paths(paths, store_dir, namespace="shared", limit=200, dry_run=False):
    """Run the pipeline. Returns an IngestReport; writes only when not dry_run."""
    store_dir = Path(store_dir)
    report = IngestReport()
    files = []
    for raw in paths:
        p = Path(raw)
        files.extend(sorted(f for f in p.rglob("*") if f.is_file()) if p.is_dir() else [p])

    candidates, queue = [], {}
    for f in files:
        if f.suffix.lower() in (".md", ".markdown"):
            result = _doc_candidate(f, report)
            if result is not None:
                candidates.append(result)
        else:
            _log_candidates(f, queue, report)

    report.candidates = len(candidates)
    candidates.sort(key=lambda item: item[0], reverse=True)  # substance first
    kept = candidates[:limit]
    report.dropped_by_cap = len(candidates) - len(kept)
    report.queue_items = min(len(queue), MAX_QUEUE_ITEMS)
    report.queue_occurrences = sum(item["count"] for item in queue.values())

    if not dry_run:
        target = store_dir / "knowledge" / namespace
        for _, entry in kept:
            target.mkdir(parents=True, exist_ok=True)
            path = target / f"{entry.id}.md"
            if not path.exists():  # idempotency: same content, same id, no rewrite
                path.write_text(entry.to_markdown())
                report.entries_written += 1
        if queue:
            ranked = sorted(queue.values(), key=lambda i: i["count"], reverse=True)
            lines = [
                "# FixDoc ingest queue",
                "",
                "Symptoms found in your logs that have no documented fix yet,",
                "most frequent first. Complete them by asking your agent:",
                '"walk me through the ingest queue and record a fix for each one',
                'I can answer."',
                "",
            ]
            for item in ranked[:MAX_QUEUE_ITEMS]:
                where = f" ({item['address']})" if item["address"] else ""
                lines.append(f"- [ ] {item['count']}x {item['code']}{where}: {item['excerpt']}")
            (store_dir / ".fixdoc").mkdir(parents=True, exist_ok=True)
            (store_dir / ".fixdoc" / "ingest-queue.md").write_text("\n".join(lines) + "\n")
    else:
        report.entries_written = len(kept)  # what WOULD be written

    return report
