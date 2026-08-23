"""Search command for fixdoc CLI."""

import click

from ..storage import FixRepository
from ..formatter import fix_to_markdown


@click.command()
@click.argument("query")
@click.option("--limit", "-l", type=int, default=None, help="Max results to show")
@click.option(
    "--tags",
    "-t",
    "tag_filter",
    default=None,
    help="Comma-separated tags to filter by (AND by default)",
)
@click.option("--any-tags", is_flag=True, help="Match ANY tag instead of ALL tags")
@click.option(
    "--any", "match_any", is_flag=True, help="Match ANY query word instead of ALL (OR mode)"
)
@click.pass_context
def search(ctx, query: str, limit: int, tag_filter: str, any_tags: bool, match_any: bool):
    """
    Search your fixes by keyword.

    Searches across issue, resolution, error excerpt, tags, and notes.
    By default, all query words must match (AND). Use --any for OR matching.

    \b
    Examples:
        fixdoc search "security group"
        fixdoc search "timeout connection" --any
        fixdoc search "timeout" --tags terraform,aws
        fixdoc search "rbac" --tags azure --any-tags
    """
    config = ctx.obj["config"]
    limit = limit if limit is not None else config.display.search_result_limit

    repo = FixRepository(ctx.obj["base_path"])
    all_fixes = repo.list_all()

    # Filter by query (multi-word AND or OR)
    results = [f for f in all_fixes if f.matches(query, match_any=match_any)]

    # Filter by tags if provided
    if tag_filter:
        required_tags = [t.strip() for t in tag_filter.split(",") if t.strip()]
        results = [f for f in results if f.matches_tags(required_tags, match_any=any_tags)]

    if not results:
        click.echo(f"No fixes found matching '{query}'")
        return

    click.echo(f"Found {len(results)} fix(es) matching '{query}':\n")

    for fix in results[:limit]:
        click.echo(f"  {fix.summary()}")

    if len(results) > limit:
        click.echo(f"\n  ... and {len(results) - limit} more. Use --limit to see more.")

    click.echo(f"\nRun `fixdoc show <fix-id>` for full details.")


@click.command()
@click.argument("fix_id")
@click.pass_context
def show(ctx, fix_id: str):
    """
    Show full details of a fix.

    Accepts full or partial fix ID.

    \b
    Example:
        fixdoc show a1b2c3d4
    """
    repo = FixRepository(ctx.obj["base_path"])
    fix = repo.get(fix_id)

    if not fix:
        click.echo(f"No fix found with ID: '{fix_id}'")
        raise SystemExit(1)

    click.echo(fix_to_markdown(fix))
