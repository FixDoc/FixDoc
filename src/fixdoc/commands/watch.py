"""Watch command — wraps a command and captures errors on failure."""

import os
import subprocess
import sys
import threading
import uuid
from typing import Optional

import click

from ..parsers import detect_and_parse
from ..parsers.base import ParsedError
from ..pending import (
    PendingEntry,
    PendingStore,
    _command_family,
    pending_entry_from_parsed_error,
)
from ..storage import FixRepository
from .capture_handlers import (
    capture_single_error,
    capture_single_k8s_error,
    handle_piped_input,
)
from ._resolve_flow import resolve_pending_entries


def _capture_error_for_watch(err: ParsedError, tags: Optional[str],
                             repo: FixRepository, config) -> Optional:
    """Route a single error through the appropriate capture function."""
    if err.error_type in ("kubectl", "helm", "kubernetes"):
        return capture_single_k8s_error(err, err.raw_output, tags, repo, config)
    return capture_single_error(err, err.raw_output, tags, repo, config)


@click.command()
@click.argument("command", nargs=-1, required=True)
@click.option("--tags", "-t", default=None, help="Tags to apply to captured fix.")
@click.option(
    "--no-prompt",
    is_flag=True,
    default=False,
    help="Skip confirmation and auto-defer all errors on failure.",
)
@click.pass_context
def watch(ctx, command, tags, no_prompt):
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
                store.save(entry)
                deferred_entries.append(entry)

        n = len(deferred_entries)

        if no_prompt:
            click.echo(f"Apply failed. {n} error(s) deferred to pending.")
            sys.exit(exit_code)

        # Defer summary card
        click.echo(f"\nApply failed with {n} error(s). Deferred to pending.")
        for i, entry in enumerate(deferred_entries, 1):
            resource = entry.resource_address or entry.short_message[:60]
            code = f" {entry.error_code}" if entry.error_code else ""
            click.echo(f"  {i}. [{resource}]{code}")
        click.echo("I'll ask what fixed these on your next successful run.")
        click.echo("[c] capture one now  [s] skip")

        choice = click.prompt("", default="s", show_default=False).strip().lower()

        if choice == "c":
            click.echo("\nWhich error?")
            for i, entry in enumerate(deferred_entries, 1):
                resource = entry.resource_address or entry.short_message[:60]
                code = f" ({entry.error_code})" if entry.error_code else ""
                click.echo(f"  {i}. {resource}{code}")
            idx = click.prompt("Error number", type=int, default=1)
            if 1 <= idx <= len(deferred_entries):
                selected_entry = deferred_entries[idx - 1]
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
            resolve_pending_entries(matches, repo, config, store)
            # Nudge about older sessions from the same directory
            older = [e for e in store.find_by_cwd(cwd) if e.session_id != resolved_session_id]
            if older:
                sessions = {e.session_id for e in older}
                click.echo(
                    f"\nThere are {len(older)} older deferred error(s) in this directory "
                    f"({len(sessions)} prior session(s)). Run `fixdoc resolve` to review them."
                )

    sys.exit(exit_code)
