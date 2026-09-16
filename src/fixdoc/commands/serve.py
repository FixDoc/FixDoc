"""fixdoc serve — run the MCP server over stdio.

Harnesses (Claude Code, Cursor) spawn this as a subprocess per their MCP
config; it reads JSON-RPC on stdin and answers on stdout, so this command
must never print to stdout itself.
"""

import sqlite3
from pathlib import Path

import click
import yaml

from fixdoc.core.embedding import DEFAULT_MODEL, get_embedder, resolve_model
from fixdoc.core.index import Index
from fixdoc.mcp_server import FixDocServer


@click.command()
@click.option(
    "--store",
    "store_dir",
    default=".",
    help="Repo root containing knowledge/ (default: current directory).",
)
@click.option(
    "--model",
    default=None,
    help=f"Embedding model name (default: .fixdoc/config.yaml, then {DEFAULT_MODEL}).",
)
@click.option(
    "--namespace",
    default="shared",
    show_default=True,
    help="Namespace directory record_fix writes into.",
)
def serve(store_dir, model, namespace):
    """Serve the four FixDoc MCP tools over stdio."""
    root = Path(store_dir)
    try:
        model = resolve_model(root, model)
        embed_fn = get_embedder(model)
        with Index(root / ".fixdoc-index", embed_fn, model) as index:
            FixDocServer(root / "knowledge", index, namespace=namespace).run()
    except (
        click.UsageError,
        OSError,
        ValueError,
        RuntimeError,
        yaml.YAMLError,
        sqlite3.Error,
    ) as exc:
        if isinstance(exc, sqlite3.Error):
            message = "Database operation failed; check index permissions and other writers."
        elif isinstance(exc, yaml.YAMLError):
            message = "Invalid YAML in .fixdoc/config.yaml."
        else:
            message = str(exc)
        raise click.ClickException(message) from exc
