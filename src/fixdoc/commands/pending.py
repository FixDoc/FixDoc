"""Pending command — manage deferred errors."""

from typing import Optional

import click

from ..pending import PendingStore
from ..storage import FixRepository
from .capture_handlers import handle_piped_input


@click.group(invoke_without_command=True)
@click.option(
    "--all", "show_all", is_flag=True, default=False, help="Include self-explanatory errors."
)
@click.pass_context
def pending(ctx, show_all):
    """List and manage deferred (pending) errors.

    Shows all pending errors from .fixdoc-pending at the git root.
    """
    if ctx.invoked_subcommand is None:
        _list_pending(show_all=show_all)


def _list_pending(show_all: bool = False) -> None:
    """List all pending errors."""
    store = PendingStore()
    entries = store.list_all(include_self_explanatory=show_all)

    if not entries:
        if not show_all:
            all_entries = store.list_all(include_self_explanatory=True)
            if all_entries:
                click.echo(
                    f"No pending errors ({len(all_entries)} self-explanatory hidden. Use --all to show)."
                )
                return
        click.echo("No pending errors.")
        return

    click.echo(f"\n{'#':>3}  {'Error ID':<14}  {'Resource':<30}  {'Code':<20}  {'Deferred At'}")
    click.echo("─" * 95)

    entries.sort(key=lambda e: e.deferred_at or "", reverse=True)

    for i, entry in enumerate(entries, 1):
        resource = entry.resource_address or "—"
        if len(resource) > 30:
            resource = resource[:27] + "..."
        code = entry.error_code or "—"
        if len(code) > 20:
            code = code[:17] + "..."
        # Show date portion only
        deferred = entry.deferred_at[:19] if entry.deferred_at else "—"
        click.echo(f"{i:>3}  {entry.error_id:<14}  {resource:<30}  {code:<20}  {deferred}")

    if not show_all:
        all_count = len(store.list_all(include_self_explanatory=True))
        hidden = all_count - len(entries)
        if hidden:
            click.echo(f"\n{len(entries)} pending error(s) ({hidden} self-explanatory hidden).\n")
        else:
            click.echo(f"\n{len(entries)} pending error(s).\n")
    else:
        click.echo(f"\n{len(entries)} pending error(s).\n")


@pending.command("capture")
@click.argument("error_id_or_number")
@click.pass_context
def pending_capture(ctx, error_id_or_number):
    """Capture a pending error by ID (or prefix) or list number."""
    store = PendingStore()
    entry = _resolve_entry(store, error_id_or_number)

    if not entry:
        click.echo(f"No pending error matching '{error_id_or_number}'.", err=True)
        return

    base_path = ctx.obj["base_path"]
    config = ctx.obj["config"]
    repo = FixRepository(base_path)

    click.echo(f"Capturing pending error: {entry.error_id}")
    click.echo(f"  {entry.short_message}\n")

    fix = handle_piped_input(entry.error_excerpt, tags=entry.tags, repo=repo, config=config)

    if fix:
        repo.save(fix)
        store.remove(entry.error_id)
        click.echo(f"\nFix saved: {fix.id[:8]}")
        click.echo("Removed from pending.")
    else:
        click.echo("No fix created. Entry remains in pending.")


@pending.command("clear")
def pending_clear():
    """Remove all pending entries."""
    store = PendingStore()
    count = store.clear()

    if count == 0:
        click.echo("No pending errors to clear.")
    else:
        click.echo(f"Cleared {count} pending error(s).")


@pending.command("remove")
@click.argument("error_id_or_number")
def pending_remove(error_id_or_number):
    """Remove a single pending entry without capturing."""
    store = PendingStore()
    entry = _resolve_entry(store, error_id_or_number)

    if not entry:
        click.echo(f"No pending error matching '{error_id_or_number}'.", err=True)
        return

    store.remove(entry.error_id)
    click.echo(f"Removed: {entry.error_id} ({entry.short_message[:60]})")


def _resolve_entry(store: PendingStore, id_or_number: str) -> Optional:
    """Resolve an entry by list number (1-based) or error_id prefix."""
    # Try as number first
    try:
        num = int(id_or_number)
        entries = store.list_all()
        if 1 <= num <= len(entries):
            return entries[num - 1]
    except ValueError:
        pass

    # Try as error_id prefix
    return store.get(id_or_number)
