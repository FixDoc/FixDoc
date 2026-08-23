"""fixdoc serve — run the MCP server over stdio.

Harnesses (Claude Code, Cursor) spawn this as a subprocess per their MCP
config; it reads JSON-RPC on stdin and answers on stdout, so this command
must never print to stdout itself.
"""

from pathlib import Path

import click

from fixdoc.core.embedding import DEFAULT_MODEL, get_embedder
from fixdoc.core.index import Index
from fixdoc.mcp_server import FixDocServer


@click.command()
@click.option(
    "--store",
    "store_dir",
    default=".",
    help="Repo root containing knowledge/ (default: current directory).",
)
@click.option("--model", default=DEFAULT_MODEL, show_default=True, help="Embedding model name.")
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
        embed_fn = get_embedder(model)
    except RuntimeError as exc:
        raise click.ClickException(str(exc))
    index = Index(root / ".fixdoc-index", embed_fn, model)
    FixDocServer(root / "knowledge", index, namespace=namespace).run()
