"""fixdoc import-slack — drain resolved incident threads into the store.

Model-extracted (the emoji convention is dead), confidence-gated by the
team's own rubric, everything quarantined. Requires a Slack bot token
(SLACK_TOKEN, read-only scopes) and a model key: this importer is
model-mandatory by design.
"""

import os
from pathlib import Path

import click
import yaml

from fixdoc.ingestion.slack_extract import (
    DEFAULT_IMPORT_MODEL,
    DEFAULT_THRESHOLD,
    extract_thread,
)
from fixdoc.ingestion.slack_import import import_slack_threads


@click.command("import-slack")
@click.option(
    "--channel", "channels", multiple=True, required=True, help="Channel ID (C...). Repeatable."
)
@click.option(
    "--store",
    "store_dir",
    default=".",
    help="Repo root containing knowledge/ (default: current directory).",
)
@click.option("--namespace", default="shared", show_default=True)
@click.option(
    "--since", "since_days", default=90, show_default=True, help="How many days of history to scan."
)
@click.option(
    "--limit",
    default=200,
    show_default=True,
    type=click.IntRange(1, 1000),
    help="Max entries this run; highest-confidence extractions are kept.",
)
@click.option(
    "--threshold",
    default=None,
    type=click.FloatRange(0.0, 1.0),
    help=f"Confidence gate override (config import.confidence_threshold, "
    f"then {DEFAULT_THRESHOLD}).",
)
@click.option(
    "--token", envvar="SLACK_TOKEN", default=None, help="Slack bot token (or SLACK_TOKEN env var)."
)
@click.option("--dry-run", is_flag=True, help="Report what would happen; write nothing.")
def import_slack(channels, store_dir, namespace, since_days, limit, threshold, token, dry_run):
    """Import resolved incident threads from Slack into the knowledge store."""
    if not token:
        raise click.ClickException(
            "no Slack token: set SLACK_TOKEN (bot token with channels:history, "
            "channels:read, users:read) or pass --token. Create the app from "
            "docs/slack-app-manifest.yaml."
        )

    config_path = Path(store_dir) / ".fixdoc" / "config.yaml"
    config = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    import_config = (config or {}).get("import") or {}
    model = import_config.get("model") or DEFAULT_IMPORT_MODEL
    key_env = import_config.get("api_key_env", "ANTHROPIC_API_KEY")
    api_key = os.environ.get(key_env)
    base_url = import_config.get("base_url")
    gate = (
        threshold
        if threshold is not None
        else float(import_config.get("confidence_threshold", DEFAULT_THRESHOLD))
    )
    rubric = import_config.get("confidence_rubric")
    provider = import_config.get("provider", "anthropic")
    if provider == "openai-compatible" and not base_url:
        raise click.ClickException(
            "import.provider: openai-compatible requires import.base_url "
            "(e.g. http://localhost:11434/v1 for a local Ollama)."
        )
    if provider == "openai-compatible":
        api_key = api_key  # optional for local endpoints
    elif not api_key:
        raise click.ClickException(
            f"the Slack importer is model-extracted and needs a key: set {key_env} "
            "(or configure import.api_key_env in .fixdoc/config.yaml)."
        )

    def extractor_fn(thread_text, rubric_text):
        return extract_thread(
            thread_text,
            rubric=rubric_text,
            model=model,
            api_key=api_key,
            base_url=base_url,
            provider=provider,
        )

    report = import_slack_threads(
        channels=list(channels),
        store_dir=store_dir,
        token=token,
        since_days=since_days,
        limit=limit,
        threshold=gate,
        rubric=rubric,
        namespace=namespace,
        dry_run=dry_run,
        extractor_fn=extractor_fn,
    )

    verb = "would write" if dry_run else "wrote"
    click.echo(
        f"{verb} {report.entries_written} quarantined entries "
        f"({report.extracted} extracted from {report.threads_scanned} threads, "
        f"model {model}, gate {gate})"
    )
    if report.unresolved:
        click.echo(f"  {report.unresolved} threads had no stated resolution — skipped")
    if report.below_threshold:
        click.echo(
            f"  {len(report.below_threshold)} extractions below the {gate} "
            "confidence gate — not written (rerun with a lower --threshold "
            "to include):"
        )
        for permalink, confidence in report.below_threshold[:10]:
            click.echo(f"    {confidence:.2f} {permalink}")
    if report.dropped_by_cap:
        click.echo(
            f"  {report.dropped_by_cap} above-gate extractions beyond --limit "
            "dropped (highest confidence kept); re-run to drain the next wave"
        )
    if report.failed:
        click.echo(f"  {report.failed} threads failed extraction and were skipped")
    if report.redactions:
        detail = ", ".join(f"{k} x{v}" for k, v in sorted(report.redactions.items()))
        click.echo(f"redacted before the model saw anything: {detail}")
    if not dry_run and report.entries_written:
        click.echo(
            "\nNext: review each entry (the Notes carry the thread permalink), "
            "fill placeholders, set status: validated."
        )
