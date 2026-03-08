"""Resolve command — document what fixed deferred errors in this directory."""

import os

import click

from ..pending import PendingStore
from ..storage import FixRepository
from ._resolve_flow import resolve_pending_entries


@click.command()
@click.pass_context
def resolve(ctx):
    """Document what fixed your deferred errors in this directory."""
    cwd = os.getcwd()
    base_path = ctx.obj["base_path"]
    config = ctx.obj["config"]
    repo = FixRepository(base_path)
    store = PendingStore()

    matches = store.find_by_cwd(cwd)

    if not matches:
        all_entries = store.list_all()
        if all_entries:
            click.echo(
                f"No pending errors for current directory "
                f"({len(all_entries)} pending in other directories)."
            )
        else:
            click.echo("No pending errors to resolve.")
        return

    resolve_pending_entries(matches, repo, config, store)
