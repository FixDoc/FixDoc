"""Import command — import closed fixes from Jira, ServiceNow, Notion, or Slack."""

from pathlib import Path
from typing import Optional

import click

from ..classifier import MEMORY_TYPES, classify_memory_type
from ..importers.base import ImportResult, is_high_signal, parse_csv, parse_json
from ..importers import jira, servicenow, notion, slack
from ..storage import FixRepository

_TYPE_SHORTHAND = {"f": "fix", "c": "check", "p": "playbook", "i": "insight"}


def _resolve_type_shorthand(val: str) -> str:
    """Resolve single-letter shorthand to full memory type name."""
    resolved = _TYPE_SHORTHAND.get(val, val)
    return resolved if resolved in MEMORY_TYPES else "fix"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_existing_source_tags(repo: FixRepository) -> set:
    """Load all source:* tags from existing fixes for duplicate detection."""
    existing = repo.list_all()
    return {
        tag.strip()
        for fix in existing
        for tag in (fix.tags or "").split(",")
        if tag.strip().startswith("source:")
    }


def _parse_extra_tags(tags_str: Optional[str]) -> list:
    if not tags_str:
        return []
    return [t.strip() for t in tags_str.split(",") if t.strip()]


def _print_summary(result: ImportResult, system: str) -> None:
    """Print the import summary table."""
    click.echo("")
    click.echo("─" * 50)
    click.echo(f"  Import summary ({system})" + (" [DRY RUN]" if result.dry_run else ""))
    click.echo("─" * 50)
    click.echo(f"  imported     : {result.imported}")
    click.echo(f"  skipped      : {result.skipped}")
    click.echo(f"  duplicates   : {result.duplicates}")
    click.echo(f"  low-signal   : {result.low_signal}")
    click.echo(f"  bad rows     : {result.bad_rows}")
    if result.tag_counts:
        click.echo("")
        click.echo("  Top tags:")
        top = sorted(result.tag_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        for tag, count in top:
            click.echo(f"    {tag}: {count}")
    click.echo("─" * 50)


def _record_tags(result: ImportResult, tags_str: Optional[str]) -> None:
    if not tags_str:
        return
    for tag in tags_str.split(","):
        tag = tag.strip()
        if tag:
            result.tag_counts[tag] = result.tag_counts.get(tag, 0) + 1


def _show_card(index: int, total: int, fix, source_id: str, system: str) -> None:
    """Display a review card for a fix candidate."""
    click.echo(f"\n[{index}/{total}] {source_id}")
    click.echo(f"  issue      : {fix.issue[:80]}{'...' if len(fix.issue) > 80 else ''}")
    click.echo(f"  resolution : {fix.resolution[:80]}{'...' if len(fix.resolution) > 80 else ''}")
    click.echo(f"  tags       : {fix.tags or ''}")
    click.echo(f"  type       : {fix.memory_type}")
    click.echo(f"  source     : {system} / {source_id}")


def _extract_source_id(fix) -> str:
    """Extract source_id from fix's source: tag."""
    if not fix.tags:
        return ""
    for tag in fix.tags.split(","):
        tag = tag.strip()
        if tag.startswith("source:"):
            parts = tag.split(":", 2)
            return parts[2] if len(parts) == 3 else ""
    return ""


def _ensure_source_tag(tags_str: Optional[str], source_tag: str) -> str:
    """Ensure source: tag is present in tags string (idempotent re-append)."""
    if not tags_str:
        return source_tag
    tags = [t.strip() for t in tags_str.split(",") if t.strip()]
    # Remove any existing source: tags
    tags = [t for t in tags if not t.startswith("source:")]
    tags.append(source_tag)
    return ",".join(tags)


def _find_source_tag(fix) -> Optional[str]:
    """Find the source:system:id tag on a fix."""
    if not fix.tags:
        return None
    for tag in fix.tags.split(","):
        tag = tag.strip()
        if tag.startswith("source:"):
            return tag
    return None


# ---------------------------------------------------------------------------
# Review flow (interactive)
# ---------------------------------------------------------------------------


def _review_flow(
    fixes: list,
    extra_bad_rows: int,
    repo: FixRepository,
    result: ImportResult,
    system: str,
    dry_run: bool,
) -> None:
    """Interactive review: y/e/s/a/q per fix."""
    existing_source_tags = _load_existing_source_tags(repo)
    result.bad_rows += extra_bad_rows
    total = len(fixes)

    i = 0
    while i < total:
        fix = fixes[i]
        source_tag = _find_source_tag(fix)

        # Duplicate check
        if source_tag and source_tag in existing_source_tags:
            result.duplicates += 1
            i += 1
            continue

        source_id = _extract_source_id(fix)
        _show_card(i + 1, total, fix, source_id, system)

        if dry_run:
            prompt_choices = "[y]es / [s]kip / [q]uit"
        else:
            prompt_choices = "[y]es / [e]dit / [s]kip / [a]ccept remaining / [q]uit"

        choice = click.prompt(f"Import? {prompt_choices}", default="y").strip().lower()

        if choice == "q":
            result.skipped += total - i - 1
            break
        elif choice == "s":
            result.skipped += 1
        elif choice == "y":
            if not dry_run:
                repo.save(fix)
                if source_tag:
                    existing_source_tags.add(source_tag)
            _record_tags(result, fix.tags)
            result.imported += 1
        elif choice == "e" and not dry_run:
            # Edit mode
            new_issue = click.prompt("  issue", default=fix.issue)
            new_resolution = click.prompt("  resolution", default=fix.resolution)
            new_tags_raw = click.prompt("  tags", default=fix.tags or "")

            # Re-classify if resolution changed
            if new_resolution != fix.resolution:
                type_default = classify_memory_type(new_resolution)
            else:
                type_default = fix.memory_type
            new_type = (
                click.prompt(
                    "  type [f]ix/[c]heck/[p]laybook/[i]nsight",
                    default=type_default,
                )
                .strip()
                .lower()
            )
            fix.memory_type = _resolve_type_shorthand(new_type)

            # Re-append source tag after edit
            if source_tag:
                new_tags = _ensure_source_tag(new_tags_raw, source_tag)
            else:
                new_tags = new_tags_raw

            fix.issue = new_issue[:300]
            fix.resolution = new_resolution[:3000]
            fix.tags = new_tags

            repo.save(fix)
            if source_tag:
                existing_source_tags.add(source_tag)
            _record_tags(result, fix.tags)
            result.imported += 1
        elif choice == "a" and not dry_run:
            # Accept remaining
            apply_filter = click.confirm("Apply low-signal filter to remaining?", default=True)
            # Process remaining (including current)
            for remaining_fix in fixes[i:]:
                r_source_tag = _find_source_tag(remaining_fix)
                if r_source_tag and r_source_tag in existing_source_tags:
                    result.duplicates += 1
                    continue
                if apply_filter and not is_high_signal(remaining_fix):
                    result.low_signal += 1
                    continue
                repo.save(remaining_fix)
                if r_source_tag:
                    existing_source_tags.add(r_source_tag)
                _record_tags(result, remaining_fix.tags)
                result.imported += 1
            break
        else:
            # Unrecognised or 'e' in dry_run — treat as skip
            result.skipped += 1

        i += 1


# ---------------------------------------------------------------------------
# Auto flow
# ---------------------------------------------------------------------------


def _auto_flow(
    fixes: list,
    extra_bad_rows: int,
    repo: FixRepository,
    result: ImportResult,
    dry_run: bool,
) -> None:
    """Auto mode: low-signal filter + bulk import."""
    existing_source_tags = _load_existing_source_tags(repo)
    result.bad_rows += extra_bad_rows

    for fix in fixes:
        source_tag = _find_source_tag(fix)

        if source_tag and source_tag in existing_source_tags:
            result.duplicates += 1
            continue

        if not is_high_signal(fix):
            result.low_signal += 1
            continue

        if not dry_run:
            repo.save(fix)
            if source_tag:
                existing_source_tags.add(source_tag)
        _record_tags(result, fix.tags)
        result.imported += 1


# ---------------------------------------------------------------------------
# Shared option decorator factory
# ---------------------------------------------------------------------------


def _shared_options(allow_description_option: bool = False):
    """Return a decorator that adds shared import options to a Click command."""

    def decorator(f):
        decorators = [
            click.argument("file", type=click.Path(exists=True)),
            click.option(
                "--closed/--no-closed",
                default=True,
                help="Only import closed issues (default: yes)",
            ),
            click.option(
                "--auto",
                is_flag=True,
                default=False,
                help="Auto mode: skip review, apply low-signal filter",
            ),
            click.option(
                "--dry-run",
                is_flag=True,
                default=False,
                help="Parse and report without saving",
            ),
            click.option("--max", "max_rows", type=int, default=None, help="Max rows to process"),
            click.option(
                "--tags",
                "extra_tags",
                type=str,
                default=None,
                help="Extra tags to apply to all imports (comma-separated)",
            ),
        ]
        if allow_description_option:
            decorators.append(
                click.option(
                    "--allow-description-as-resolution",
                    is_flag=True,
                    default=False,
                    help="Use Description as resolution fallback when Close/Resolution/Work notes are empty",
                )
            )
        # Apply decorators in reverse order (Click stacks them)
        import functools

        for dec in reversed(decorators):
            f = dec(f)
        return f

    return decorator


# ---------------------------------------------------------------------------
# CLI group and subcommands
# ---------------------------------------------------------------------------


@click.group(name="import")
def import_group():
    """Import closed fixes from Jira, ServiceNow, Notion, or Slack.

    \b
    Examples:
        fixdoc import jira export.csv --closed --auto
        fixdoc import jira backup.json --closed --dry-run
        fixdoc import servicenow incidents.json --allow-description-as-resolution
        fixdoc import notion --token TOKEN --database DB_ID --auto
        fixdoc import slack --token $SLACK_TOKEN --channel C0123456
    """


@import_group.command("jira")
@click.argument("file", type=click.Path(exists=True))
@click.option(
    "--closed/--no-closed",
    default=True,
    help="Only import closed issues (default: yes)",
)
@click.option(
    "--auto",
    is_flag=True,
    default=False,
    help="Auto mode: skip review, apply low-signal filter",
)
@click.option("--dry-run", is_flag=True, default=False, help="Parse and report without saving")
@click.option("--max", "max_rows", type=int, default=None, help="Max rows to process")
@click.option(
    "--tags",
    "extra_tags",
    type=str,
    default=None,
    help="Extra tags for all imports (comma-separated)",
)
@click.pass_context
def jira_import(ctx, file, closed, auto, dry_run, max_rows, extra_tags):
    """Import fixes from a Jira CSV or JSON export.

    FILE can be a .csv or .json file (auto-detected by extension).

    \b
    Examples:
        fixdoc import jira issues.csv --closed --auto
        fixdoc import jira backup.json --closed --dry-run
    """
    path = Path(file)
    extra = _parse_extra_tags(extra_tags)

    click.echo(f"[import] Reading {path.name} ...")

    is_json = path.suffix.lower() == ".json"
    bad_rows_parse = 0

    if is_json:
        raw_rows = parse_json(path)
    else:
        raw_rows, bad_rows_parse = parse_csv(path)

    click.echo(f"[import] {len(raw_rows)} rows read.")

    fixes, extract_bad = jira.extract(
        raw_rows,
        closed_only=closed,
        extra_tags=extra,
        max_count=max_rows,
        is_json=is_json,
    )
    total_bad = bad_rows_parse + extract_bad

    click.echo(f"[import] {len(fixes)} candidate fix(es) after filtering.")

    repo = FixRepository(ctx.obj["base_path"])
    result = ImportResult(dry_run=dry_run)

    if auto:
        _auto_flow(fixes, total_bad, repo, result, dry_run)
    else:
        _review_flow(fixes, total_bad, repo, result, "jira", dry_run)

    _print_summary(result, "jira")


@import_group.command("servicenow")
@click.argument("file", type=click.Path(exists=True))
@click.option(
    "--closed/--no-closed",
    default=True,
    help="Only import closed incidents (default: yes)",
)
@click.option(
    "--auto",
    is_flag=True,
    default=False,
    help="Auto mode: skip review, apply low-signal filter",
)
@click.option("--dry-run", is_flag=True, default=False, help="Parse and report without saving")
@click.option("--max", "max_rows", type=int, default=None, help="Max rows to process")
@click.option(
    "--tags",
    "extra_tags",
    type=str,
    default=None,
    help="Extra tags for all imports (comma-separated)",
)
@click.option(
    "--allow-description-as-resolution",
    is_flag=True,
    default=False,
    help="Use Description as resolution fallback when Close/Resolution/Work notes are empty",
)
@click.pass_context
def snow_import(
    ctx,
    file,
    closed,
    auto,
    dry_run,
    max_rows,
    extra_tags,
    allow_description_as_resolution,
):
    """Import fixes from a ServiceNow JSON export.

    \b
    Examples:
        fixdoc import servicenow incidents.json --closed --auto
        fixdoc import servicenow incidents.json --allow-description-as-resolution
    """
    path = Path(file)
    extra = _parse_extra_tags(extra_tags)

    click.echo(f"[import] Reading {path.name} ...")

    raw_rows = parse_json(path)
    click.echo(f"[import] {len(raw_rows)} rows read.")

    fixes, extract_bad = servicenow.extract(
        raw_rows,
        closed_only=closed,
        extra_tags=extra,
        max_count=max_rows,
        allow_description=allow_description_as_resolution,
    )
    total_bad = extract_bad

    click.echo(f"[import] {len(fixes)} candidate fix(es) after filtering.")

    repo = FixRepository(ctx.obj["base_path"])
    result = ImportResult(dry_run=dry_run)

    if auto:
        _auto_flow(fixes, total_bad, repo, result, dry_run)
    else:
        _review_flow(fixes, total_bad, repo, result, "servicenow", dry_run)

    _print_summary(result, "servicenow")


@import_group.command("notion")
@click.option(
    "--token",
    required=True,
    envvar="NOTION_TOKEN",
    help="Notion integration token (or set NOTION_TOKEN env var).",
)
@click.option("--database", required=True, help="Notion database ID.")
@click.option(
    "--title-field",
    default=None,
    help="Property name for issue title (default: Name/Title/Summary/Incident/Issue).",
)
@click.option(
    "--resolution-field",
    default=None,
    help="Property name for resolution (default: Resolution/Fix/Notes/Description/Postmortem/…).",
)
@click.option(
    "--status-field",
    default=None,
    help="Property name for status (default: Status/State/Ticket Status/Progress).",
)
@click.option(
    "--done-values",
    default=None,
    help="Comma-separated status values treated as closed (default: Done,Closed,Resolved,Fixed,…).",
)
@click.option(
    "--closed/--no-closed",
    default=True,
    help="Only import closed records (default: yes).",
)
@click.option(
    "--auto",
    is_flag=True,
    default=False,
    help="Auto mode: skip review, apply low-signal filter.",
)
@click.option("--dry-run", is_flag=True, default=False, help="Parse and report without saving.")
@click.option("--max", "max_count", type=int, default=None, help="Max pages to process.")
@click.option(
    "--tags",
    "extra_tags",
    type=str,
    default=None,
    help="Extra tags for all imports (comma-separated).",
)
@click.pass_context
def notion_cmd(
    ctx,
    token,
    database,
    title_field,
    resolution_field,
    status_field,
    done_values,
    closed,
    auto,
    dry_run,
    max_count,
    extra_tags,
):
    """Import fixes from a Notion database via the Notion API."""
    extra = _parse_extra_tags(extra_tags)

    click.echo("[import] Fetching pages from Notion database ...")
    try:
        pages = notion.fetch_pages(token, database, max_count=max_count)
    except RuntimeError as exc:
        click.echo(f"[import] Error: {exc}", err=True)
        ctx.exit(1)
        return

    click.echo(f"[import] {len(pages)} pages fetched.")

    fixes, skipped_open, skipped_missing, bad_rows = notion.extract(
        pages,
        closed_only=closed,
        extra_tags=extra,
        max_count=max_count,
        title_field=title_field,
        resolution_field=resolution_field,
        status_field=status_field,
        done_values=done_values,
        fetch_blocks_fn=lambda pid: notion.fetch_page_blocks(token, pid),
    )

    click.echo(f"[import] {skipped_open} skipped as open/in-progress")
    click.echo(f"[import] {skipped_missing} skipped for missing title or resolution")
    click.echo(f"[import] {len(fixes)} candidate fix(es) after filtering")

    repo = FixRepository(ctx.obj["base_path"])
    result = ImportResult(dry_run=dry_run)
    result.bad_rows = bad_rows

    if auto:
        _auto_flow(fixes, 0, repo, result, dry_run)
    else:
        _review_flow(fixes, 0, repo, result, "notion", dry_run)

    _print_summary(result, "notion")


@import_group.command("slack")
@click.option(
    "--token",
    required=True,
    envvar="SLACK_TOKEN",
    help="Slack bot token (xoxb-...). Also reads SLACK_TOKEN env var.",
)
@click.option("--channel", "channels", multiple=True, help="Channel ID (C0XXXXXXX). Repeatable.")
@click.option(
    "--channel-name",
    "channel_names",
    multiple=True,
    help="Channel name (resolves to ID). Alternative to --channel.",
)
@click.option(
    "--issue-reaction",
    default="red_circle",
    help="Reaction on root message marking it as an issue (default: red_circle).",
)
@click.option(
    "--resolution-reaction",
    default="white_check_mark",
    help="Reaction on reply marking it as the resolution (default: white_check_mark).",
)
@click.option(
    "--oldest",
    default=90,
    type=int,
    help="Only scan threads from the last N days (default: 90).",
)
@click.option(
    "--auto",
    is_flag=True,
    default=False,
    help="Auto mode: skip review, apply low-signal filter.",
)
@click.option("--dry-run", is_flag=True, default=False, help="Parse and report without saving.")
@click.option("--max", "max_count", type=int, default=None, help="Max threads to import.")
@click.option(
    "--tags",
    "extra_tags",
    type=str,
    default=None,
    help="Extra tags for all imports (comma-separated).",
)
@click.pass_context
def slack_import(
    ctx,
    token,
    channels,
    channel_names,
    issue_reaction,
    resolution_reaction,
    oldest,
    auto,
    dry_run,
    max_count,
    extra_tags,
):
    """Import fixes from Slack channels using the two-emoji convention.

    Mark the root message with a reaction (default: red_circle) for the issue,
    and mark a reply with a reaction (default: white_check_mark) for the resolution.

    \b
    Examples:
        fixdoc import slack --token $SLACK_TOKEN --channel C0123456 --auto
        fixdoc import slack --token $SLACK_TOKEN --channel-name terraform-help --dry-run
        fixdoc import slack --token $SLACK_TOKEN --channel C01 --channel C02
    """
    extra = _parse_extra_tags(extra_tags)

    # Resolve channel names to IDs
    channel_ids = list(channels)
    for name in channel_names:
        click.echo(f"[import] Resolving channel name: {name} ...")
        resolved = slack.resolve_channel_name(token, name)
        if not resolved:
            click.echo(f"[import] Error: channel '{name}' not found.", err=True)
            ctx.exit(1)
            return
        channel_ids.append(resolved)

    if not channel_ids:
        click.echo(
            "[import] Error: provide at least one --channel or --channel-name.",
            err=True,
        )
        ctx.exit(1)
        return

    user_cache = {}  # type: dict

    def _fetch_user(uid, cache):
        return slack.fetch_user_display_name(token, uid, cache)

    # Collect threads across all channels
    all_threads = []
    channel_name_map = {}  # type: dict

    for cid in channel_ids:
        click.echo(f"[import] Scanning channel {cid} (last {oldest} days) ...")
        messages = slack.fetch_channel_messages(token, cid, oldest_days=oldest)

        # Filter to issue-marked messages with replies
        issue_msgs = [
            m
            for m in messages
            if slack.has_reaction(m, issue_reaction) and m.get("reply_count", 0) >= 1
        ]

        if not issue_msgs:
            click.echo(f"[import] No issue-marked threads found in {cid}.")
            continue

        click.echo(f"[import] Found {len(issue_msgs)} issue-marked thread(s) in {cid}.")

        # Resolve channel name for notes (best effort)
        ch_name = cid
        for cn in channel_names:
            ch_name = cn.lstrip("#")
            break
        channel_name_map[cid] = ch_name

        for msg in issue_msgs:
            replies = slack.fetch_thread_replies(token, cid, msg["ts"])
            all_threads.append(
                {
                    "root": msg,
                    "replies": replies,
                    "channel_id": cid,
                }
            )

    if not all_threads:
        click.echo("[import] No issue-marked threads found.")
        _print_summary(ImportResult(dry_run=dry_run), "slack")
        return

    # Determine channel name for notes
    ch_name = "slack"
    if channel_names:
        ch_name = channel_names[0].lstrip("#")
    elif len(channel_ids) == 1:
        ch_name = channel_ids[0]

    fixes, skipped_no_res, bad_rows = slack.extract(
        all_threads,
        extra_tags=extra,
        max_count=max_count,
        channel_name=ch_name,
        resolution_reaction=resolution_reaction,
        fetch_user_fn=_fetch_user,
    )

    if skipped_no_res:
        click.echo(
            f"[import] Skipped {skipped_no_res} thread(s) with issue marker but no resolution marker."
        )
    click.echo(f"[import] {len(fixes)} candidate fix(es) after filtering.")

    repo = FixRepository(ctx.obj["base_path"])
    result = ImportResult(dry_run=dry_run)
    result.bad_rows = bad_rows

    if auto:
        _auto_flow(fixes, 0, repo, result, dry_run)
    else:
        _review_flow(fixes, 0, repo, result, "slack", dry_run)

    _print_summary(result, "slack")
