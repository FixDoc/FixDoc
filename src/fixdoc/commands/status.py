"""fixdoc status — is FixDoc working, and is it earning its keep?

The customer-legible answers to three questions, straight from ground truth:
did agents actually call the server (events log), what does the store hold
and what needs review (the files), and what did it prove (occurrences — the
ROI number). Also the gap report: searches that found nothing are the
documentation backlog, ranked by real demand.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import click

from fixdoc.core.events import read_events
from fixdoc.core.models import Entry

WINDOW_DAYS = 30


def _entries(store_dir):
    knowledge = store_dir / "knowledge"
    if not knowledge.is_dir():
        return
    for path in sorted(knowledge.rglob("*.md")):
        try:
            yield Entry.from_markdown(path.read_text())
        except Exception:
            continue  # doctor's job, not status's


def _recent_events(store_dir):
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    for event in read_events(store_dir / ".fixdoc-index"):
        try:
            when = datetime.fromisoformat(event["ts"])
        except (KeyError, ValueError):
            continue
        if when >= cutoff:
            yield event


@click.command("status")
@click.option(
    "--store",
    "store_dir",
    default=".",
    help="Repo root containing knowledge/ (default: current directory).",
)
def status(store_dir):
    """Store health, agent activity, proof of value, and the gap report."""
    store_dir = Path(store_dir)
    counts, occurrences, quarantined = {}, 0, []
    for entry in _entries(store_dir):
        counts[entry.status] = counts.get(entry.status, 0) + 1
        if entry.status == "validated":
            occurrences += entry.occurrences or 0
        elif entry.status == "quarantined":
            quarantined.append(entry)

    summary = " · ".join(
        f"{counts.get(s, 0)} {s}"
        for s in ("validated", "quarantined", "deprecated")
        if counts.get(s) or s != "deprecated"
    )
    click.echo(f"Store    {summary or 'empty — seed it: fixdoc ingest / import-slack'}")
    click.echo(f"Proof    validated fixes have resolved {occurrences} incidents to date")

    if quarantined:
        quarantined.sort(key=lambda e: e.created or "9999")
        oldest = quarantined[0]
        click.echo(
            f"Review   {len(quarantined)} awaiting review — oldest: {oldest.id} "
            f"(created {oldest.created or 'unknown'})"
        )
        click.echo("         review each entry, then: fixdoc promote <id>")

    searches = served = empty = confirms = records = tokens = 0
    gaps = []
    for event in _recent_events(store_dir):
        payload = event.get("payload", {})
        if event["type"] == "retrieval_served":
            searches += 1
            tokens += payload.get("tokens_served", 0)
            if payload.get("served"):
                served += 1
            else:
                empty += 1
                query = payload.get("query", "")
                if query and query not in gaps:
                    gaps.append(query)
        elif event["type"] == "entry_confirmed":
            confirms += 1
        elif event["type"] == "entry_recorded":
            records += 1

    click.echo(f"Activity (last {WINDOW_DAYS}d)")
    click.echo(f"         {searches} searches · {served} served results · {empty} found nothing")
    click.echo(f"         {confirms} confirms · {records} records")
    click.echo(f"         ~{tokens} tokens of validated context served to agents")
    if searches == 0:
        click.echo(
            "         no agent calls in the window — check /mcp in your harness, "
            "and that the fixdoc server is approved"
        )

    if gaps:
        click.echo("Gaps     searches that found nothing — the documentation backlog:")
        for query in gaps[-5:]:
            click.echo(f"         - {query}")
