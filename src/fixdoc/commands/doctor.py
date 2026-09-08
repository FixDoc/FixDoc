"""fixdoc doctor — why isn't it working?

Checks the traps we've actually seen bite (starting with the silent one:
entry-named files whose ids aren't valid hex are invisible to the index,
with no error anywhere). FAILs exit nonzero; warnings inform.
"""

import re
from pathlib import Path

import click
import yaml

from fixdoc.core.models import TYPE_PREFIXES, Entry

# Two nets with a deliberate gap between them. _ENTRY_LIKE_RE is the loose
# net: "smells like an entry" (fx_/pb_/in_ + anything). _VALID_NAME_RE is the
# strict spec the index enforces: prefix + exactly 8 hex chars. A file caught
# by the loose net but not the strict one is the silent trap this command
# exists for — a human meant it to be an entry, the index ignores it, and
# nothing anywhere says so. READMEs and notes match neither, so they pass
# through untouched.
_ENTRY_LIKE_RE = re.compile(r"^(?:%s)_.+\.md$" % "|".join(TYPE_PREFIXES.values()))
_VALID_NAME_RE = re.compile(r"^(?:%s)_[0-9a-f]{8}\.md$" % "|".join(TYPE_PREFIXES.values()))


@click.command("doctor")
@click.option(
    "--store",
    "store_dir",
    default=".",
    help="Repo root containing knowledge/ (default: current directory).",
)
def doctor(store_dir):
    """Check the store, config, and index for the problems that bite silently."""
    store_dir = Path(store_dir)
    failures = 0

    knowledge = store_dir / "knowledge"
    if knowledge.is_dir():
        click.echo("ok:   knowledge/ exists")
    else:
        click.echo("FAIL: no knowledge/ directory — run: fixdoc init")
        failures += 1

    config_path = store_dir / ".fixdoc" / "config.yaml"
    if config_path.exists():
        try:
            config = yaml.safe_load(config_path.read_text()) or {}
            click.echo(
                f"ok:   config.yaml parses (spec_version " f"{config.get('spec_version', '?')})"
            )
        except yaml.YAMLError as exc:
            click.echo(f"FAIL: config.yaml does not parse: {exc}")
            failures += 1
    else:
        click.echo("warn: no .fixdoc/config.yaml (defaults apply) — fixdoc init writes one")

    if knowledge.is_dir():
        for path in sorted(knowledge.rglob("*.md")):
            name = path.name
            if not _ENTRY_LIKE_RE.match(name):
                continue  # READMEs and notes are fine
            if not _VALID_NAME_RE.match(name):
                click.echo(
                    f"warn: {name} looks like an entry but its id is not "
                    f"prefix + 8 hex chars — the index silently IGNORED it. "
                    f"Rename it (e.g. via record_fix) to make it retrievable."
                )
                continue
            try:
                entry = Entry.from_markdown(path.read_text())
            except Exception as exc:
                click.echo(f"warn: {name} does not parse as an entry ({exc})")
                continue
            if entry.id != path.stem:
                click.echo(
                    f"warn: {name} has frontmatter id {entry.id} — filename and "
                    f"id must match ({path.stem} vs {entry.id}); the index keys "
                    f"on the file, retrieval returns the frontmatter id."
                )

    try:
        import fastembed  # noqa: F401

        click.echo("ok:   embedding backend installed (fastembed)")
    except ImportError:
        click.echo(
            "warn: fastembed not installed — serve/search need it: " 'pip install "fixdoc[embed]"'
        )

    if (store_dir / ".fixdoc-index" / "index.db").exists():
        click.echo("ok:   index present (rebuilds itself when stale)")
    else:
        click.echo("note: no index yet — the first search builds it")

    if failures:
        raise SystemExit(1)
    click.echo("\nhealthy.")
