"""The day-0 ingestion pipeline: resolution-bearing documents in, entries out.

One honest rule shapes everything: entries are only born from sources that
contain remediations (runbooks, postmortems, incident writeups). Anything
without a fix-type section is notes, not knowledge, and is skipped. Slack
threads and tickets arrive via their importers (same rule); environment
insights come from the human interview, not from files.

Every extracted string passes through redaction before it is written.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from fixdoc.core.models import Entry

from .classify import classify_memory_type
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


@dataclass
class IngestReport:
    entries_written: int = 0
    candidates: int = 0
    dropped_by_cap: int = 0
    skipped_no_resolution: int = 0
    non_documents: int = 0
    redactions: dict = field(default_factory=dict)
    classification: str = "rules"  # "model" when worthiness_fn decided
    classification_error: str = ""  # set when the model path failed and rules took over


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


def _expand(path, knowledge_dir):
    """Files under a directory argument, minus hidden trees (.git, .cache) and
    the store's own knowledge/ (ingesting the store into itself would clone
    every entry under a new id). Explicitly listed files are never filtered:
    naming a file is authorization enough."""
    if not path.is_dir():
        yield path
        return
    for f in sorted(path.rglob("*")):
        if not f.is_file():
            continue
        if any(part.startswith(".") for part in f.relative_to(path).parts):
            continue
        if knowledge_dir in f.resolve().parents:
            continue
        yield f


def ingest_paths(paths, store_dir, namespace="shared", limit=200, dry_run=False):
    """Run the pipeline. Returns an IngestReport; writes only when not dry_run."""
    store_dir = Path(store_dir)
    report = IngestReport()
    knowledge_dir = (store_dir / "knowledge").resolve()
    files = []
    for raw in paths:
        files.extend(_expand(Path(raw), knowledge_dir))

    candidates = []
    for f in files:
        if f.suffix.lower() in (".md", ".markdown"):
            result = _doc_candidate(f, report)
            if result is not None:
                candidates.append(result)
        else:
            report.non_documents += 1  # logs etc: no remediation, not ingested

    report.candidates = len(candidates)
    candidates.sort(key=lambda item: item[0], reverse=True)  # substance first
    kept = candidates[:limit]
    report.dropped_by_cap = len(candidates) - len(kept)

    if not dry_run:
        target = store_dir / "knowledge" / namespace
        for _, entry in kept:
            target.mkdir(parents=True, exist_ok=True)
            path = target / f"{entry.id}.md"
            if not path.exists():  # idempotency: same content, same id, no rewrite
                path.write_text(entry.to_markdown())
                report.entries_written += 1
    else:
        report.entries_written = len(kept)  # what WOULD be written

    return report
