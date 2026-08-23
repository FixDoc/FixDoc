"""List and stats commands for fixdoc CLI."""

import click

from ..storage import FixRepository


@click.command(name="list")
@click.option("--limit", "-l", type=int, default=None, help="Max fixes to show")
@click.pass_context
def list_fixes(ctx, limit: int):
    """
    List all captured fixes.

    Shows a summary of each fix, most recent first.
    """
    config = ctx.obj["config"]
    limit = limit if limit is not None else config.display.list_result_limit

    repo = FixRepository(ctx.obj["base_path"])
    fixes = repo.list_all()

    if not fixes:
        click.echo("No fixes captured yet. Run `fixdoc capture` to add one.")
        return

    fixes.sort(key=lambda f: f.created_at, reverse=True)

    click.echo(f"Total fixes: {len(fixes)}\n")

    for fix in fixes[:limit]:
        click.echo(f"  {fix.summary()}")

    if len(fixes) > limit:
        click.echo(f"\n  ... and {len(fixes) - limit} more. Use --limit to see more.")


@click.command()
@click.pass_context
def stats(ctx):
    """Show statistics about your fix database."""
    config = ctx.obj["config"]
    repo = FixRepository(ctx.obj["base_path"])
    fixes = repo.list_all()

    if not fixes:
        click.echo("No fixes captured yet.")
        return

    all_tags = []
    for fix in fixes:
        if fix.tags:
            all_tags.extend([t.strip() for t in fix.tags.split(",")])

    tag_counts = {}
    for tag in all_tags:
        tag_counts[tag] = tag_counts.get(tag, 0) + 1

    click.echo(" Fix Database Statistics\n")
    click.echo(f"  Total fixes: {len(fixes)}")
    click.echo(f"  Fixes with tags: {sum(1 for f in fixes if f.tags)}")
    click.echo(f"  Fixes with error excerpts: {sum(1 for f in fixes if f.error_excerpt)}")

    if tag_counts:
        click.echo("\n  Top tags:")
        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        for tag, count in sorted_tags[: config.display.top_tags_limit]:
            click.echo(f"    {tag}: {count}")
