"""The Slack import engine: threads in, confidence-gated quarantined entries out.

Order of operations is the security story: threads are REDACTED before the
model ever sees them (secrets must not reach the API either), extraction is
scored against the team's rubric, the threshold gate decides what is written,
and everything that survives lands quarantined with a permalink so the
reviewer can check the source in one click.
"""

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

from fixdoc.core.models import Entry

from .classify import classify_memory_type
from .redact import redact
from .slack_extract import DEFAULT_THRESHOLD, extract_thread
from . import slack_api

PLACEHOLDER = "(not stated in the thread — add during review)"


@dataclass
class SlackImportReport:
    threads_scanned: int = 0
    extracted: int = 0
    unresolved: int = 0
    failed: int = 0
    below_threshold: list = field(default_factory=list)  # (permalink, confidence)
    entries_written: int = 0
    dropped_by_cap: int = 0
    redactions: dict = field(default_factory=dict)


def _permalink(channel, ts):
    return f"https://slack.com/archives/{channel}/p{ts.replace('.', '')}"


def _thread_text(root, replies):
    lines = [f"<{root.get('user', '?')}>: {root.get('text', '')}"]
    lines += [f"<{r.get('user', '?')}>: {r.get('text', '')}" for r in replies]
    return "\n".join(lines)


def _build_entry(channel, ts, extraction):
    fix_text = extraction["fix"] or PLACEHOLDER
    symptom = extraction["symptom"] or PLACEHOLDER
    entry_type = classify_memory_type(fix_text)
    if entry_type == "playbook":
        sections = {
            "When to use": symptom,
            "Steps": fix_text,
            "Verification": extraction["verification"] or PLACEHOLDER,
        }
        prefix = "pb"
    else:
        entry_type = "fix"  # insight extraction from threads: not yet; fix is honest default
        sections = {
            "Symptom": symptom,
            "Root cause": extraction["root_cause"] or PLACEHOLDER,
            "Fix": fix_text,
            "Verification": extraction["verification"] or PLACEHOLDER,
        }
        prefix = "fx"
    sections["Notes"] = (
        f"Imported from Slack {channel} · {_permalink(channel, ts)} " f"· thread {ts}"
    )
    # Identity from the thread itself: re-runs and overlapping --since windows
    # can never duplicate an import.
    digest = hashlib.sha256(f"{channel}_{ts}".encode()).hexdigest()[:8]
    return Entry(
        id=f"{prefix}_{digest}",
        type=entry_type,
        title=extraction["title"] or symptom[:90],
        sections=sections,
        status="quarantined",
        confidence=extraction["confidence"],
        created="",
    )


def import_slack_threads(
    channels,
    store_dir,
    token,
    since_days=90,
    limit=200,
    threshold=DEFAULT_THRESHOLD,
    rubric=None,
    namespace="shared",
    dry_run=False,
    extractor_fn=None,
    fetch_messages_fn=None,
    fetch_replies_fn=None,
):
    """Run the import. All IO is injectable, so tests never touch a network."""
    store_dir = Path(store_dir)
    report = SlackImportReport()
    fetch_messages = fetch_messages_fn or slack_api.fetch_channel_messages
    fetch_replies = fetch_replies_fn or slack_api.fetch_thread_replies
    extractor = extractor_fn or extract_thread
    oldest = str(time.time() - since_days * 86400)

    candidates = []
    for channel in channels:
        for message in fetch_messages(token, channel, oldest=oldest):
            if not message.get("reply_count"):
                continue  # prefilter: a resolution needs at least one reply
            report.threads_scanned += 1
            ts = message["ts"]
            replies = fetch_replies(token, channel, ts)
            text, counts = redact(_thread_text(message, replies))
            for name, n in counts.items():
                report.redactions[name] = report.redactions.get(name, 0) + n
            try:
                extraction = extractor(text, rubric)
            except Exception:
                report.failed += 1  # one bad thread never fails the run
                continue
            if not extraction.get("resolved"):
                report.unresolved += 1
                continue
            report.extracted += 1
            confidence = extraction.get("confidence", 0.0)
            if confidence < threshold:
                report.below_threshold.append((_permalink(channel, ts), confidence))
                continue
            candidates.append(_build_entry(channel, ts, extraction))

    candidates.sort(key=lambda e: e.confidence or 0.0, reverse=True)
    kept = candidates[:limit]
    report.dropped_by_cap = len(candidates) - len(kept)

    if dry_run:
        report.entries_written = len(kept)
        return report
    target = store_dir / "knowledge" / namespace
    for entry in kept:
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{entry.id}.md"
        if not path.exists():
            path.write_text(entry.to_markdown())
            report.entries_written += 1
    return report
