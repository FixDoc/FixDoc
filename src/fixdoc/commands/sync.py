"""Sync commands for sharing fixes via Git."""

from pathlib import Path
from typing import Optional

import click

from ..config import ConfigManager
from ..git import GitOperations, GitError, SyncStatus
from ..storage import FixRepository
from ..sync_engine import SyncEngine


def get_sync_context(base_path: Optional[Path] = None):
    """Get shared objects for sync commands."""
    if base_path is None:
        from ..config import resolve_base_path

        base_path = resolve_base_path()
    return {
        "repo": FixRepository(base_path),
        "config_manager": ConfigManager(base_path),
        "git": GitOperations(base_path),
    }


@click.group()
@click.pass_context
def sync(ctx):
    """Sync fixes with a shared Git repository."""
    pass


@sync.command()
@click.argument("repo_url")
@click.option("--branch", "-b", default="main", help="Branch to sync with")
@click.option("--name", "-n", default=None, help="Your name for attribution")
@click.option("--email", "-e", default=None, help="Your email for attribution")
@click.pass_context
def init(ctx, repo_url: str, branch: str, name: Optional[str], email: Optional[str]):
    """
    Initialize sync with a shared Git repository.

    \b
    Example:
        fixdoc sync init git@github.com:mycompany/infra-fixes.git
        fixdoc sync init https://github.com/mycompany/infra-fixes.git
    """
    ctx = get_sync_context(ctx.obj["base_path"])
    config_manager = ctx["config_manager"]
    git = ctx["git"]

    config = config_manager.load()
    if config.sync.remote_url:
        if not click.confirm(
            f"Sync already configured with {config.sync.remote_url}. Reconfigure?"
        ):
            click.echo("Aborted.")
            return

    if not name:
        name = click.prompt("Your name (for attribution)")
    if not email:
        email = click.prompt("Your email")

    try:
        if not git.is_git_repo():
            click.echo("Initializing Git repository...")
            git.init()

        if git.has_remote("origin"):
            current_url = git.remote_get_url("origin")
            if current_url != repo_url:
                click.echo(f"Updating remote URL from {current_url} to {repo_url}")
                git.remote_set_url("origin", repo_url)
        else:
            click.echo(f"Adding remote: {repo_url}")
            git.remote_add("origin", repo_url)

        config.sync.remote_url = repo_url
        config.sync.branch = branch
        config.user.name = name
        config.user.email = email
        config_manager.save(config)

        try:
            click.echo(f"Fetching from remote...")
            git.fetch("origin")

            status = git.get_status("origin", branch)
            if status.commits_behind > 0:
                click.echo(f"Pulling {status.commits_behind} commits from remote...")
                git.pull("origin", branch)

                engine = SyncEngine(ctx["repo"], git, config_manager)
                pulled = engine.rebuild_json_from_markdown()
                if pulled:
                    click.echo(f"Imported {len(pulled)} fixes from team repository.")

        except GitError:
            click.echo("Remote repository appears to be empty. Ready for first push.")

        click.echo("")
        click.echo("Sync initialized successfully!")
        click.echo(f"  Repository: {repo_url}")
        click.echo(f"  Branch: {branch}")
        click.echo(f"  Author: {name} <{email}>")
        click.echo("")
        click.echo("Next steps:")
        click.echo("  fixdoc sync push   - Push your fixes to the team repo")
        click.echo("  fixdoc sync pull   - Pull fixes from teammates")
        click.echo("  fixdoc sync status - Check sync status")

    except GitError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@sync.command()
@click.option("--message", "-m", default=None, help="Commit message")
@click.option("--all", "-a", "push_all", is_flag=True, help="Push all local fixes")
@click.pass_context
def push(ctx, message: Optional[str], push_all: bool):
    """
    Push local fixes to the shared repository.

    \b
    Example:
        fixdoc sync push
        fixdoc sync push -m "Added storage account fix"
    """
    ctx = get_sync_context(ctx.obj["base_path"])
    engine = SyncEngine(ctx["repo"], ctx["git"], ctx["config_manager"])

    if not ctx["config_manager"].is_sync_configured():
        click.echo("Sync not configured. Run 'fixdoc sync init <repo-url>' first.", err=True)
        raise SystemExit(1)

    config = ctx["config_manager"].load()
    if config.sync.auto_pull:
        click.echo("Auto-pulling latest changes...")
        pull_result = engine.execute_pull()
        if not pull_result.success and pull_result.conflicts:
            click.echo(
                "Conflicts detected. Please resolve with 'fixdoc sync pull' first.", err=True
            )
            raise SystemExit(1)

    fixes = engine.prepare_push(push_all=push_all)

    if not fixes:
        click.echo("No new or modified fixes to push.")
        return

    click.echo(f"Pushing {len(fixes)} fix(es)...")
    for fix in fixes:
        click.echo(f"  {fix.id[:8]} - {fix.issue[:40]}...")

    result = engine.execute_push(fixes, commit_message=message)

    if result.success:
        if result.pushed_fixes:
            click.echo(f"Successfully pushed {len(result.pushed_fixes)} fix(es).")
        elif result.error_message:
            click.echo(result.error_message)
    else:
        click.echo(f"Push failed: {result.error_message}", err=True)
        if "rejected" in (result.error_message or "").lower():
            click.echo("Hint: Run 'fixdoc sync pull' first to get the latest changes.")
        raise SystemExit(1)


@sync.command()
@click.option("--force", "-f", is_flag=True, help="Overwrite local changes on conflict")
@click.pass_context
def pull(ctx, force: bool):
    """
    Pull fixes from the shared repository.

    \b
    Example:
        fixdoc sync pull
        fixdoc sync pull --force  # Accept all remote changes
    """
    ctx = get_sync_context(ctx.obj["base_path"])
    engine = SyncEngine(ctx["repo"], ctx["git"], ctx["config_manager"])

    if not ctx["config_manager"].is_sync_configured():
        click.echo("Sync not configured. Run 'fixdoc sync init <repo-url>' first.", err=True)
        raise SystemExit(1)

    click.echo("Pulling from remote...")
    result = engine.execute_pull(force=force)

    if result.success:
        if result.pulled_fixes:
            click.echo(f"Successfully pulled/updated {len(result.pulled_fixes)} fix(es).")
            for fix_id in result.pulled_fixes[:5]:
                click.echo(f"  {fix_id[:8]}")
            if len(result.pulled_fixes) > 5:
                click.echo(f"  ... and {len(result.pulled_fixes) - 5} more")
        else:
            click.echo("Already up to date.")
    elif result.conflicts:
        click.echo(f"Conflicts detected in {len(result.conflicts)} fix(es):", err=True)
        for conflict in result.conflicts:
            click.echo(f"  {conflict.fix_id[:8]} - {conflict.conflict_type.value}")
        click.echo("")
        click.echo("To resolve:")
        click.echo("  fixdoc sync pull --force  # Accept all remote changes")
        click.echo("  # Or manually edit the conflicting fixes and push again")
        raise SystemExit(1)
    else:
        click.echo(f"Pull failed: {result.error_message}", err=True)
        raise SystemExit(1)


@sync.command()
@click.pass_context
def status(ctx):
    """
    Show sync status (ahead/behind commits, local changes).

    \b
    Example:
        fixdoc sync status
    """
    ctx = get_sync_context(ctx.obj["base_path"])
    engine = SyncEngine(ctx["repo"], ctx["git"], ctx["config_manager"])

    status_info = engine.get_sync_status()

    if not status_info["configured"]:
        click.echo("Sync not configured.")
        click.echo("")
        click.echo("To set up sync, run:")
        click.echo("  fixdoc sync init <repo-url>")
        return

    click.echo("Sync Status")
    click.echo("=" * 40)
    click.echo(f"Repository: {status_info['remote_url']}")
    click.echo(f"Branch: {status_info['branch']}")
    click.echo("")

    status_val = status_info["status"]
    if status_val == SyncStatus.UP_TO_DATE.value:
        click.echo("Status: Up to date")
    elif status_val == SyncStatus.AHEAD.value:
        click.echo(f"Status: {status_info['commits_ahead']} commit(s) ahead")
    elif status_val == SyncStatus.BEHIND.value:
        click.echo(f"Status: {status_info['commits_behind']} commit(s) behind")
    elif status_val == SyncStatus.DIVERGED.value:
        click.echo(
            f"Status: Diverged ({status_info['commits_ahead']} ahead, "
            f"{status_info['commits_behind']} behind)"
        )
    elif status_val == SyncStatus.NO_REMOTE.value:
        click.echo("Status: No remote configured")

    click.echo("")
    click.echo(f"Total fixes: {status_info['total_fixes']}")
    click.echo(f"Pushable fixes: {status_info['pushable_fixes']}")
    click.echo(f"Private fixes: {status_info['private_fixes']}")

    if status_info["local_changes"]:
        click.echo("")
        click.echo("Local changes:")
        for change in status_info["local_changes"][:5]:
            click.echo(f"  {change}")
        if len(status_info["local_changes"]) > 5:
            click.echo(f"  ... and {len(status_info['local_changes']) - 5} more")

    click.echo("")
    if status_info["pushable_fixes"] > 0:
        click.echo("Run 'fixdoc sync push' to share your fixes.")
    if status_val == SyncStatus.BEHIND.value:
        click.echo("Run 'fixdoc sync pull' to get team updates.")
