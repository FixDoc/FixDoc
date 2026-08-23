from typing import Optional

import click

from ..models import Fix
from ..storage import FixRepository


@click.command()
@click.argument("fix_id")
@click.option("--issue", "-i", type=str, help="Update the issue description")
@click.option("--resolution", "-r", type=str, help="Update the resolution")
@click.option("--tags", "-t", type=str, help="Update tags")
@click.option("--notes", "-n", type=str, help="Update notes")
@click.option("--error", "-e", type=str, help="Update error excerpt")
@click.option("--interactive", "-I", is_flag=True, help="Edit all fields interactively")
@click.pass_context
def edit(
    ctx,
    fix_id: str,
    issue: Optional[str],
    resolution: Optional[str],
    tags: Optional[str],
    notes: Optional[str],
    error: Optional[str],
    interactive: bool,
):
    """
    Edit an existing fix.

    \b
    Examples:
        fixdoc edit a1b2c3d4 --resolution "New fix details"
        fixdoc edit a1b2c3d4 --tags "storage,rbac,new_tag"
        fixdoc edit a1b2c3d4 -I  # Interactive mode
    """
    repo = FixRepository(ctx.obj["base_path"])

    fix = repo.get(fix_id)
    if not fix:
        click.echo(f"No fix found with ID starting with '{fix_id}'")
        raise SystemExit(1)

    if interactive:
        fix = _interactive_edit(fix)
    else:
        fix = _flag_edit(fix, issue, resolution, tags, notes, error)

    if fix:
        fix.touch()
        repo.save(fix)
        click.echo(f"✓ Updated fix: {fix.id[:8]}")


def _interactive_edit(fix: Fix) -> Fix:
    """Edit all fields interactively."""
    click.echo(f"Editing fix: {fix.id[:8]}\n")
    click.echo("Press Enter to keep current value.\n")

    new_issue = click.prompt("Issue", default=fix.issue, show_default=True)
    new_resolution = click.prompt("Resolution", default=fix.resolution, show_default=True)

    new_error = click.prompt(
        "Error excerpt",
        default=fix.error_excerpt or "",
        show_default=True if fix.error_excerpt else False,
    )

    new_tags = click.prompt(
        "Tags",
        default=fix.tags or "",
        show_default=True if fix.tags else False,
    )

    new_notes = click.prompt(
        "Notes",
        default=fix.notes or "",
        show_default=True if fix.notes else False,
    )

    fix.issue = new_issue
    fix.resolution = new_resolution
    fix.error_excerpt = new_error or None
    fix.tags = new_tags or None
    fix.notes = new_notes or None

    return fix


def _flag_edit(
    fix: Fix,
    issue: Optional[str],
    resolution: Optional[str],
    tags: Optional[str],
    notes: Optional[str],
    error: Optional[str],
) -> Optional[Fix]:
    """Edit specific fields via flags."""
    if not any([issue, resolution, tags, notes, error]):
        click.echo("No changes specified. Use flags or -I for interactive mode.")
        click.echo("Run 'fixdoc edit --help' for options.")
        return None

    if issue:
        fix.issue = issue
    if resolution:
        fix.resolution = resolution
    if tags:
        fix.tags = tags
    if notes:
        fix.notes = notes
    if error:
        fix.error_excerpt = error

    return fix
