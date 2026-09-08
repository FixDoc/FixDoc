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
@click.argument("entry_ids", nargs=-1, required=True)
@click.option(
    "--store",
    "store_dir",
    default=".",
    help="Repo root containing knowledge/ (default: current directory).",
)
def promote(entry_ids, store_dir):
    """Promote quarantined entries to validated (id or unique prefix)."""
    store_dir = Path(store_dir)
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
