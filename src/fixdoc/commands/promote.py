"""fixdoc promote — the human act that makes knowledge retrievable.

The sed-free version of editing status: quarantined -> validated. It only
changes the one field (round-tripped through the Entry model, so nothing
else in the file moves) and reminds the human to commit — on a team, the
commit IS the review record.
"""

from pathlib import Path

import click

from fixdoc.core.models import Entry


def _find(store_dir, wanted):
    matches = []
    knowledge = store_dir / "knowledge"
    if knowledge.is_dir():
        for path in sorted(knowledge.rglob("*.md")):
            if path.stem == wanted or path.stem.startswith(wanted):
                matches.append(path)
    return matches


@click.command("promote")
@click.argument("entry_ids", nargs=-1)
@click.option(
    "--store",
    "store_dir",
    default=".",
    help="Repo root containing knowledge/ (default: current directory).",
)
@click.option(
    "--all",
    "promote_all",
    is_flag=True,
    help="Promote every quarantined entry. Bulk promotion skips "
    "per-entry review, so it asks for confirmation.",
)
@click.option("--yes", is_flag=True, help="Skip the --all confirmation prompt.")
def promote(entry_ids, store_dir, promote_all, yes):
    """Promote quarantined entries to validated (ids, unique prefixes, or --all)."""
    store_dir = Path(store_dir)
    if promote_all and entry_ids:
        raise click.ClickException("pass ids OR --all, not both")
    if not promote_all and not entry_ids:
        raise click.ClickException("pass entry ids, or --all for the whole queue")

    if promote_all:
        knowledge = store_dir / "knowledge"
        queue = []
        for path in sorted(knowledge.rglob("*.md")) if knowledge.is_dir() else []:
            try:
                entry = Entry.from_markdown(path.read_text())
            except Exception:
                continue
            if entry.status == "quarantined":
                queue.append(path.stem)
        if not queue:
            click.echo("nothing in quarantine — the queue is clear.")
            return
        # Review is the quality gate; bulk promotion is the human explicitly
        # vouching for the whole batch, so show what the batch IS first.
        if not yes:
            click.confirm(
                f"promote all {len(queue)} quarantined entries without " "per-entry review?",
                abort=True,
            )
        entry_ids = queue

    promoted = 0
    for wanted in entry_ids:
        matches = _find(store_dir, wanted)
        if not matches:
            raise click.ClickException(f"no entry found for {wanted!r}")
        if len(matches) > 1:
            names = ", ".join(p.stem for p in matches)
            raise click.ClickException(f"{wanted!r} is ambiguous: {names}")
        path = matches[0]
        entry = Entry.from_markdown(path.read_text())
        if entry.status == "validated":
            click.echo(f"{entry.id} is already validated")
            continue
        entry.status = "validated"
        path.write_text(entry.to_markdown())
        click.echo(f"promoted {entry.id} -> validated ({entry.title})")
        promoted += 1
    if promoted:
        click.echo(
            "\ncommit the change so your team gets it (normally as a PR) — "
            "the next search serves it immediately."
        )
