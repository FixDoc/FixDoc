"""Capture command for fixdoc CLI."""

import os
import sys
from typing import Optional

import click

from ..models import Fix
from ..storage import FixRepository
from .capture_handlers import (
    handle_interactive_capture,
    handle_piped_input,
    handle_quick_capture,
)


def _reopen_stdin_from_terminal() -> bool:
    """Reopen stdin from terminal after reading piped input."""
    try:
        # Unix/Mac
        if os.path.exists("/dev/tty"):
            sys.stdin = open("/dev/tty", "r")
            return True
    except OSError:
        pass

    try:
        # Windows
        if sys.platform == "win32":
            sys.stdin = open("CON", "r")
            return True
    except OSError:
        pass

    return False


@click.command()
@click.option(
    "--quick",
    "-q",
    type=str,
    default=None,
    help="Quick capture: 'issue | resolution'",
)
@click.option(
    "--tags",
    "-t",
    type=str,
    default=None,
    help="Tags (comma-separated)",
)
@click.pass_context
def capture(ctx, quick: Optional[str], tags: Optional[str]):
    """
    Capture a new fix.

    \b
    Pipe terraform errors:
        terraform apply 2>&1 | fixdoc capture

    \b
    Interactive:
        fixdoc capture

    \b
    Quick:
        fixdoc capture -q "issue | resolution" -t storage,rbac
    """
    config = ctx.obj["config"]
    repo = FixRepository(ctx.obj["base_path"])

    # Check for piped input
    if not sys.stdin.isatty():
        fix = _handle_piped_input(tags, repo, config)
    elif quick:
        fix = handle_quick_capture(quick, tags, repo)
    else:
        fix = handle_interactive_capture(tags, repo)

    if fix:
        # Set author from config if available
        if config.user.name and not fix.author:
            fix.author = config.user.name
            fix.author_email = config.user.email

        saved = repo.save(fix)
        click.echo(f"\n----- Fix captured: {saved.id[:8]}")
        click.echo(f"  Markdown: ~/.fixdoc/docs/{saved.id}.md")


def _handle_piped_input(
    tags: Optional[str], repo: Optional[FixRepository] = None, config=None
) -> Optional[Fix]:
    """Route piped input to appropriate handler using unified parser."""
    # Read all piped input first
    piped_input = sys.stdin.read()

    if not piped_input.strip():
        click.echo("No input received.", err=True)
        return None

    # Reopen stdin from terminal so we can prompt user
    if not _reopen_stdin_from_terminal():
        click.echo("Error: Cannot prompt for input in this environment.", err=True)
        click.echo("Use quick mode instead:", err=True)
        click.echo("  fixdoc capture -q 'issue | resolution' -t tags", err=True)
        return None

    # Use unified handler that auto-detects Terraform, K8s, Helm, etc.
    return handle_piped_input(piped_input, tags, repo, config=config)
