"""Build, rebuild, or inspect the derived knowledge index."""

import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import click
import yaml
from sqlalchemy.exc import SQLAlchemyError

from fixdoc.core.embedding import DEFAULT_MODEL, get_embedder, resolve_model
from fixdoc.core.index import Index, index_stats


def _counts(report):
    return (
        ", ".join(f"{report[key]} {key}" for key in ("added", "updated", "removed", "unchanged"))
        + f", {len(report['skipped'])} skipped"
    )


def _show_stats(report):
    if not report["exists"]:
        click.echo("No index exists. Run fixdoc index to create one.")
        return
    click.echo(f"Index: {report['path']} ({report['size_bytes']:,} bytes)")
    click.echo(f"Model: {report['embedding_model'] or 'unknown'}")
    click.echo(f"Schema: {report['schema_version'] or 'unknown'}")
    if report["needs_rebuild"]:
        click.echo("Schema needs rebuilding. Run fixdoc index.")
    counts = report["entries"]
    click.echo(f"Entries: {counts['total']}")
    for field in ("type", "status"):
        detail = ", ".join(f"{key}: {value}" for key, value in counts[f"by_{field}"].items())
        click.echo(f"  By {field}: {detail or 'none'}")
    run = report["last_run"]
    if run:
        click.echo(
            f"Last committed {run['operation']}: {run['completed_at']} "
            f"({run['duration_seconds']:.3f}s)"
        )
        click.echo(f"  {_counts(run)}")
    else:
        click.echo("Last run: unavailable")


@click.command("index")
@click.option(
    "--store",
    "store_dir",
    default=".",
    show_default=True,
    type=click.Path(path_type=Path),
    help="Repo root containing knowledge/.",
)
@click.option("--rebuild", is_flag=True, help="Regenerate all embeddings and indexed entries.")
@click.option(
    "--stats", is_flag=True, help="Inspect the index without changing it or loading a model."
)
@click.option(
    "--model",
    default=None,
    help=f"Embedding model (default: config.yaml, then {DEFAULT_MODEL}).",
)
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable JSON to stdout.")
@click.pass_context
def index_command(ctx, store_dir, rebuild, stats, model, json_output):
    """Create or incrementally update the knowledge index.

    Exit codes: 0 success, 1 failure, 2 skipped entries or invalid options.
    """
    try:
        if stats and (rebuild or model is not None):
            raise click.UsageError("--stats cannot be combined with --rebuild or --model")
        index_dir = store_dir / ".fixdoc-index"
        if stats:
            report = index_stats(index_dir)
        else:
            if not (store_dir / "knowledge").is_dir():
                raise ValueError(
                    f"No knowledge/ directory in {store_dir}. "
                    "Run fixdoc init or choose a repo with --store."
                )
            resolved_model = resolve_model(store_dir, model)
            # Keep dependency download/progress messages out of JSON stdout.
            with redirect_stdout(sys.stderr):
                embed_fn = get_embedder(resolved_model)
                with Index(index_dir, embed_fn, resolved_model) as index:
                    report = index.sync(store_dir / "knowledge", rebuild=rebuild)
    except (
        click.UsageError,
        OSError,
        ValueError,
        RuntimeError,
        yaml.YAMLError,
        SQLAlchemyError,
    ) as exc:
        code = 2 if isinstance(exc, click.UsageError) else 1
        # SQLAlchemy exception strings can include entry text in parameters.
        if isinstance(exc, SQLAlchemyError):
            message = "Database operation failed; check index permissions and other writers."
        elif isinstance(exc, yaml.YAMLError):
            message = "Invalid YAML in .fixdoc/config.yaml."
        else:
            message = str(exc)
        if json_output:
            click.echo(json.dumps({"error": message}))
        else:
            click.echo(f"Error: {message}", err=True)
        ctx.exit(code)

    if json_output:
        click.echo(json.dumps(report))
    elif stats:
        _show_stats(report)
    else:
        click.echo(f"Index updated: {_counts(report)}")
        for path in report["skipped"]:
            click.echo(f"  {path}: {report['errors'][path]}")
    if not stats and report["skipped"]:
        ctx.exit(2)
