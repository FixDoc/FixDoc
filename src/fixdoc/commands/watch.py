"""Watch command — wraps a command and captures errors on failure."""

import os
import subprocess
import sys
import threading
import uuid
from typing import Optional

import click

from ..classifier import classify_entry
from ..parsers import detect_and_parse
from ..rendering import format_suggestion_preview
from ..parsers.base import ParsedError
from ..pending import (
    PendingEntry,
    PendingStore,
    _command_family,
    pending_entry_from_parsed_error,
)
from ..storage import FixRepository
from ..suggestions import find_similar_fixes
from .capture_handlers import (
    capture_single_error,
    capture_single_k8s_error,
    handle_piped_input,
)
from ._resolve_flow import resolve_pending_entries

# Patterns that indicate a non-error exit (user cancelled, etc.)
_CANCELLED_PATTERNS = [
    "Apply cancelled.",
    "Plan cancelled.",
    "Apply canceled.",
    "Plan canceled.",
]


def _is_cancelled_apply(output_text):
    """Return True if the output indicates a cancelled (non-error) exit."""
    # Check the last few lines for cancellation messages
    tail = output_text[-200:] if len(output_text) > 200 else output_text
    return any(pattern in tail for pattern in _CANCELLED_PATTERNS)


def _track_effectiveness_success(entries, repo):
    """Increment applied_count and success_count for fixes linked to resolved errors."""
    error_ids = {e.error_id for e in entries}
    if not error_ids:
        return
    from ..models import _now_iso

    now = _now_iso()
    for fix in repo.list_all():
        if not fix.source_error_ids:
            continue
        if any(eid in error_ids for eid in fix.source_error_ids):
            fix.applied_count += 1
            fix.success_count += 1
            fix.last_applied_at = now
            fix.touch()
            repo.save(fix)


def _track_effectiveness_failure(entries, repo):
    """Increment applied_count (not success) for recurring errors."""
    error_ids = {e.error_id for e in entries}
    if not error_ids:
        return
    from ..models import _now_iso

    now = _now_iso()
    for fix in repo.list_all():
        if not fix.source_error_ids:
            continue
        if any(eid in error_ids for eid in fix.source_error_ids):
            fix.applied_count += 1
            fix.last_applied_at = now
            fix.touch()
            repo.save(fix)


def _capture_error_for_watch(
    err: ParsedError, tags: Optional[str], repo: FixRepository, config
) -> Optional:
    """Route a single error through the appropriate capture function."""
    if err.error_type in ("kubectl", "helm", "kubernetes"):
        return capture_single_k8s_error(err, err.raw_output, tags, repo, config)
    return capture_single_error(err, err.raw_output, tags, repo, config)


def _show_fix_suggestions_list(entries, repo, limit_per_error=2, max_total=6):
    """Show known fixes and return the suggestions list for reuse.

    Returns list of (entry_label, fix) tuples.
    """
    seen_fix_ids = set()
    suggestions = []

    for entry in entries:
        matches = find_similar_fixes(
            repo,
            entry.error_excerpt or entry.short_message,
            tags=entry.tags or None,
            limit=limit_per_error,
            resource_address=entry.resource_address,
            error_id=entry.error_id,
        )
        label = entry.resource_address or entry.short_message[:60]
        code = f" ({entry.error_code})" if entry.error_code else ""
        entry_label = f"{label}{code}"

        for fix in matches:
            if fix.id in seen_fix_ids:
                continue
            seen_fix_ids.add(fix.id)
            suggestions.append((entry_label, fix))
            if len(suggestions) >= max_total:
                break
        if len(suggestions) >= max_total:
            break

    if not suggestions:
        return suggestions

    click.echo("\nKnown fixes that may help:")
    current_label = None
    for entry_label, fix in suggestions:
        if entry_label != current_label:
            click.echo(f"  For {entry_label}:")
            current_label = entry_label
        preview = format_suggestion_preview(fix)
        click.echo(f"    {fix.id[:8]}: {preview}")
    click.echo("Run `fixdoc show <id>` for details.")

    return suggestions


def _diagnose_errors_inline(entries, config):
    """Show AI-generated diagnosis for deferred errors."""
    from ..diagnosis import diagnose_errors

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        click.echo("Warning: --diagnose requires ANTHROPIC_API_KEY.", err=True)
        return

    max_errors = getattr(getattr(config, "diagnosis", None), "max_errors", 3)
    model = getattr(getattr(config, "diagnosis", None), "model", "claude-haiku-4-5-20251001")

    results = diagnose_errors(entries, api_key=api_key, max_errors=max_errors, model=model)

    if not results:
        return

    click.echo("\nAI Diagnosis:")
    for entry, diagnosis in results:
        label = entry.resource_address or entry.short_message[:60]
        code = f" ({entry.error_code})" if entry.error_code else ""
        click.echo(f"  {label}{code}:")
        for line in diagnosis.strip().splitlines():
            click.echo(f"    {line}")
        click.echo()


def _maybe_notify_slack(entries, suggestions, config, command_str, notify_flag):
    """Send Slack notification if configured and there are enough matches."""
    slack_enabled = notify_flag or config.notification.slack_enabled
    if not slack_enabled:
        return

    token = os.environ.get("SLACK_TOKEN")
    if config.notification.slack_token:
        token = config.notification.slack_token
    if not token:
        click.echo(
            "Warning: Slack notification requires SLACK_TOKEN env var or config.",
            err=True,
        )
        return

    channel = config.notification.slack_channel
    if not channel:
        click.echo(
            "Warning: Slack notification requires " "notification.slack_channel in config.",
            err=True,
        )
        return

    if len(suggestions) < config.notification.slack_min_matches:
        return

    try:
        from ..notifications import post_slack_notification

        cwd = os.getcwd()
        success = post_slack_notification(
            token=token,
            channel=channel,
            entries=entries,
            suggestions=suggestions,
            cwd=cwd,
            command=command_str,
        )
        if not success:
            click.echo("Warning: Failed to send Slack notification.", err=True)
    except Exception as e:
        click.echo(f"Warning: Slack notification error: {e}", err=True)


@click.command()
@click.argument("command", nargs=-1, required=True)
@click.option("--tags", "-t", default=None, help="Tags to apply to captured fix.")
@click.option(
    "--no-prompt",
    is_flag=True,
    default=False,
    help="Skip confirmation and auto-defer all errors on failure.",
)
@click.option(
    "--diagnose",
    is_flag=True,
    default=False,
    help="Use Claude API to explain errors. Requires ANTHROPIC_API_KEY.",
)
@click.option(
    "--notify",
    is_flag=True,
    default=False,
    help="Send Slack notification when errors match known fixes.",
)
@click.pass_context
def watch(ctx, command, tags, no_prompt, diagnose, notify):
    """Run a command and capture errors on failure.

    Wraps any command, streams output normally. On failure, defers errors to
    pending. On the next successful run, offers to document what fixed them.

    Usage: fixdoc watch -- terraform apply
    """
    if not command:
        raise click.UsageError("No command provided. Usage: fixdoc watch -- <command>")

    base_path = ctx.obj["base_path"]
    config = ctx.obj["config"]
    repo = FixRepository(base_path)
    command_str = " ".join(command)
    session_id = uuid.uuid4().hex[:8]
    family = _command_family(command_str)

    captured_output = []

    def _reader(pipe):
        """Read from pipe, display to terminal, and buffer output."""
        try:
            for line in iter(pipe.readline, b""):
                decoded = line.decode("utf-8", errors="replace")
                sys.stdout.write(decoded)
                sys.stdout.flush()
                captured_output.append(decoded)
        except ValueError:
            pass
        finally:
            pipe.close()

    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except FileNotFoundError:
        click.echo(f"Command not found: {command[0]}", err=True)
        sys.exit(127)

    reader_thread = threading.Thread(target=_reader, args=(proc.stdout,))
    reader_thread.daemon = True
    reader_thread.start()

    proc.wait()
    reader_thread.join(timeout=5)

    exit_code = proc.returncode

    if exit_code != 0:
        output_text = "".join(captured_output).strip()

        if not output_text:
            sys.exit(exit_code)

        # Detect non-error exits (e.g. terraform "Apply cancelled")
        if _is_cancelled_apply(output_text):
            sys.exit(exit_code)

        cwd = os.getcwd()
        errors = detect_and_parse(output_text)
        store = PendingStore()
        store.supersede_context(cwd, family)

        if not errors:
            # Generic error — one entry
            from ..parsers.base import compute_error_id

            generic_entry = PendingEntry(
                error_id=compute_error_id(error_message=output_text),
                error_type="generic",
                short_message=output_text[:120],
                error_excerpt=output_text[:2000],
                tags=tags or "",
                command=command_str,
                cwd=cwd,
                session_id=session_id,
                command_family=family,
                kind="resource",
            )
            generic_entry.worthiness = classify_entry(generic_entry, store)
            store.save(generic_entry)
            deferred_entries = [generic_entry]
        else:
            deferred_entries = []
            for err in errors:
                entry = pending_entry_from_parsed_error(
                    err,
                    command=command_str,
                    cwd=cwd,
                    session_id=session_id,
                    command_family=family,
                )
                entry.worthiness = classify_entry(entry, store)
                store.save(entry)
                deferred_entries.append(entry)

        memory_worthy = [e for e in deferred_entries if e.worthiness == "memory_worthy"]
        self_explanatory = [e for e in deferred_entries if e.worthiness == "self_explanatory"]
        _track_effectiveness_failure(deferred_entries, repo)

        if no_prompt:
            if memory_worthy:
                click.echo(f"Apply failed. {len(memory_worthy)} error(s) deferred to pending.")
            else:
                click.echo("Apply failed.")
            if self_explanatory:
                click.echo(f"  + {len(self_explanatory)} self-explanatory error(s) also captured.")
            suggestions = _show_fix_suggestions_list(memory_worthy, repo)
            if diagnose or config.diagnosis.enabled:
                _diagnose_errors_inline(memory_worthy, config)
            _maybe_notify_slack(memory_worthy, suggestions, config, command_str, notify)
            sys.exit(exit_code)

        # Defer summary card
        click.echo(f"\nApply failed with {len(deferred_entries)} error(s).")
        if memory_worthy:
            click.echo(f"{len(memory_worthy)} deferred to pending:")
            for i, entry in enumerate(memory_worthy, 1):
                resource = entry.resource_address or entry.short_message[:60]
                code = f" {entry.error_code}" if entry.error_code else ""
                click.echo(f"  {i}. [{resource}]{code}")
        if self_explanatory:
            click.echo(f"  + {len(self_explanatory)} self-explanatory error(s) (hidden)")
        suggestions = _show_fix_suggestions_list(memory_worthy, repo)
        if diagnose or config.diagnosis.enabled:
            _diagnose_errors_inline(memory_worthy, config)
        _maybe_notify_slack(memory_worthy, suggestions, config, command_str, notify)

        if memory_worthy:
            click.echo("I'll ask what fixed these on your next successful run.")
            click.echo("[c] capture one now  [s] skip")

            choice = click.prompt("", default="s", show_default=False).strip().lower()

            if choice == "c":
                click.echo("\nWhich error?")
                for i, entry in enumerate(memory_worthy, 1):
                    resource = entry.resource_address or entry.short_message[:60]
                    code = f" ({entry.error_code})" if entry.error_code else ""
                    click.echo(f"  {i}. {resource}{code}")
                idx = click.prompt("Error number", type=int, default=1)
                if 1 <= idx <= len(memory_worthy):
                    selected_entry = memory_worthy[idx - 1]
                    if not errors or selected_entry.error_type == "generic":
                        fix = handle_piped_input(
                            selected_entry.error_excerpt,
                            tags=tags,
                            repo=repo,
                            config=config,
                        )
                    else:
                        matching_err = next(
                            (e for e in errors if e.error_id == selected_entry.error_id),
                            None,
                        )
                        if matching_err:
                            fix = _capture_error_for_watch(matching_err, tags, repo, config)
                        else:
                            fix = handle_piped_input(
                                selected_entry.error_excerpt,
                                tags=tags,
                                repo=repo,
                                config=config,
                            )
                    if fix:
                        repo.save(fix)
                        store.remove(selected_entry.error_id)
                        click.echo(f"Fix saved: {fix.id[:8]}")

        sys.exit(exit_code)

    # Success path — resolve deferred errors from recent failed runs
    if not no_prompt:
        cwd = os.getcwd()
        store = PendingStore()
        matches = store.find_latest_session(cwd, family)
        if matches:
            resolved_session_id = matches[0].session_id
            _track_effectiveness_success(matches, repo)
            resolve_pending_entries(matches, repo, config, store)
            # Auto-resolve self-explanatory entries from same session
            all_session = store.find_latest_session(cwd, family, include_self_explanatory=True)
            for e in all_session:
                if e.worthiness == "self_explanatory":
                    store.remove(e.error_id)
            # Nudge about older sessions from the same directory
            older = [e for e in store.find_by_cwd(cwd) if e.session_id != resolved_session_id]
            if older:
                sessions = {e.session_id for e in older}
                click.echo(
                    f"\nThere are {len(older)} older deferred error(s) in this directory "
                    f"({len(sessions)} prior session(s)). Run `fixdoc resolve` to review them."
                )

    sys.exit(exit_code)
