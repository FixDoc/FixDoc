"""Shared resolution flow for deferred pending errors."""

import re

import click

from ..pending import PendingEntry, PendingStore
from ..storage import FixRepository
from .capture_handlers import handle_piped_input

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)
_REQID_RE = re.compile(r"RequestID:\s*\S+", re.I)


def _normalize_error_line(short_message: str) -> str:
    s = _UUID_RE.sub("<UUID>", short_message)
    s = _REQID_RE.sub("RequestID:<UUID>", s)
    return s.strip().lower()


def _group_by_error(entries: list) -> list:
    """Group entries with same (error_code, normalized_error_line) into bundles."""
    seen: dict = {}
    order: list = []
    for entry in entries:
        if entry.error_code:
            key = (entry.error_code, _normalize_error_line(entry.short_message))
        else:
            key = (id(entry),)  # singleton — no bundling
        if key not in seen:
            seen[key] = []
            order.append(key)
        seen[key].append(entry)
    return [seen[k] for k in order]


def resolve_pending_entries(
    entries: list,
    repo: FixRepository,
    config,
    store: PendingStore,
) -> None:
    """Interactively prompt to document fixes for deferred pending errors."""
    all_ids = {e.error_id for e in entries}
    removed_ids: set = set()

    groups = _group_by_error(entries)

    click.echo(f"\n{len(entries)} deferred error(s) from a recent failed run:")
    for i, entry in enumerate(entries, 1):
        resource = entry.resource_address or entry.short_message[:60]
        code = f" ({entry.error_code})" if entry.error_code else ""
        click.echo(f"  {i}. [{resource}]{code} — deferred {entry.deferred_at[:10]}")

    click.echo()
    for group in groups:
        first = group[0]
        resource = first.resource_address or first.short_message[:60]
        code = f" ({first.error_code})" if first.error_code else ""
        if len(group) > 1:
            extras = len(group) - 1
            header = f"{resource} + {extras} more{code}"
        else:
            header = f"{resource}{code}"

        click.echo(f"\n── {header} ──")
        click.echo(f"   {first.short_message[:100]}")
        click.echo("[Enter] document fix  [s] skip  [q] quit all")
        choice = click.prompt("", default="", show_default=False).strip().lower()

        if choice == "q":
            break
        if choice == "s":
            continue

        # [Enter] — capture
        fix = handle_piped_input(
            first.error_excerpt,
            tags=first.tags or None,
            repo=repo,
            config=config,
        )
        if fix:
            repo.save(fix)
            for e in group:
                store.remove(e.error_id)
                removed_ids.add(e.error_id)
            click.echo(f"Fix saved: {fix.id[:8]}")

    # Clear-on-resolve: remove all remaining entries that were passed in
    for eid in all_ids - removed_ids:
        store.remove(eid)
