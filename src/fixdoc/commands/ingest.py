"""fixdoc ingest — day-0 seeding from resolution-bearing documents.

Runbooks, postmortems, and incident writeups become quarantined entries;
secrets are redacted before anything is written; selection is capped and
ranked by substance. Logs are deliberately not ingested: errors carry no
remediation, and entries are only born from sources that contain one.
"""

import click

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
def ingest(paths, store_dir, namespace, limit, dry_run):
    """Seed the knowledge store from runbooks, postmortems, and incident docs."""
    report = ingest_paths(paths, store_dir, namespace=namespace, limit=limit, dry_run=dry_run)
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
    if report.non_documents:
        click.echo(
            f"  {report.non_documents} non-document files ignored — logs carry "
            "no remediation; knowledge comes from docs, threads, and people"
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
    click.echo(
        "\nBest first seed: ask your agent to interview you — ten environment "
        "facts every new engineer learns the hard way, plus your five worst "
        "incidents — and record each one."
    )
