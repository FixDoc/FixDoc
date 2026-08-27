"""fixdoc ingest — day-0 seeding: feed logs and postmortems, get a store.

Documents with resolutions become quarantined entries; logs become a symptom
queue (never fabricated fixes). Secrets are redacted before anything is
written. Selection is capped and ranked by substance, because a quarantine
queue nobody can review is worth nothing.
"""

import os

import click

from fixdoc.ingestion.model_classify import DEFAULT_CLASSIFY_MODEL, model_worthiness
from fixdoc.ingestion.pipeline import ingest_paths


@click.command("ingest")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option(
    "--store",
    "store_dir",
    default=".",
    help="Repo root containing knowledge/ (default: current directory).",
)
@click.option("--namespace", default="shared", show_default=True)
@click.option(
    "--limit",
    default=200,
    show_default=True,
    type=click.IntRange(1, 1000),
    help="Max entries to create this run; the top N by substance are kept. "
    "Re-run after reviewing a batch to drain the next wave.",
)
@click.option("--dry-run", is_flag=True, help="Report what would happen; write nothing.")
@click.option(
    "--classify-model",
    default=DEFAULT_CLASSIFY_MODEL,
    show_default=True,
    help="Model used to judge which errors are knowledge-worthy "
    "(used when ANTHROPIC_API_KEY is set).",
)
def ingest(paths, store_dir, namespace, limit, dry_run, classify_model):
    """Seed the knowledge store from logs, postmortems, and incident docs."""
    worthiness_fn = None
    if os.environ.get("ANTHROPIC_API_KEY"):

        def worthiness_fn(items):
            return model_worthiness(items, model=classify_model)

    report = ingest_paths(
        paths,
        store_dir,
        namespace=namespace,
        limit=limit,
        dry_run=dry_run,
        worthiness_fn=worthiness_fn,
    )
    if report.classification == "model":
        click.echo(f"error classification: model ({classify_model})")
    elif report.classification_error:
        click.echo(
            f"model classification failed ({report.classification_error}) "
            "— falling back to rules"
        )
    elif report.queue_items or report.noise_errors:
        click.echo("error classification: rule-based " "(set ANTHROPIC_API_KEY for model-based)")
    verb = "would write" if dry_run else "wrote"
    click.echo(
        f"{verb} {report.entries_written} quarantined entries "
        f"(from {report.candidates} candidates)"
    )
    if report.dropped_by_cap:
        click.echo(
            f"  {report.dropped_by_cap} candidates beyond --limit were dropped "
            "(kept the highest-substance ones). Review this batch, then re-run "
            "to drain the next wave, or raise --limit."
        )
    if report.skipped_no_resolution:
        click.echo(
            f"  {report.skipped_no_resolution} documents had no resolution "
            "content and were skipped"
        )
    if report.queue_items:
        click.echo(
            f"symptom queue: {report.queue_items} distinct errors "
            f"({report.queue_occurrences} occurrences) -> .fixdoc/ingest-queue.md"
        )
        click.echo(
            "  logs contain symptoms, not fixes: complete the queue by asking "
            "your agent to interview you through it."
        )
    if report.noise_errors:
        click.echo(
            f"  {report.noise_errors} self-explanatory errors (typos, missing "
            "arguments) discarded as noise"
        )
    if report.redactions:
        detail = ", ".join(f"{k} x{v}" for k, v in sorted(report.redactions.items()))
        click.echo(
            f"redacted before writing: {detail} — review entries to confirm "
            "nothing sensitive remains"
        )
    if not dry_run and report.entries_written:
        click.echo(
            "\nNext: review each entry (fill placeholders, set status: "
            "validated) — quarantined entries are invisible to agents."
        )
