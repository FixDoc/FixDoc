"""Slack importer — two-emoji batch import via Slack API (uses urllib)."""

import json
import re
import time
import urllib.error
import urllib.request
from typing import Callable, List, Optional, Tuple

from .base import build_fix, detect_resource_types, normalize_tags

_SLACK_API = "https://slack.com/api"
_MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# API layer
# ---------------------------------------------------------------------------


def _slack_request(
    endpoint: str,
    token: str,
    params: Optional[dict] = None,
) -> dict:
    """GET a Slack API endpoint. Retries on 429. Raises RuntimeError on errors."""
    url = f"{_SLACK_API}/{endpoint}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        if qs:
            url = f"{url}?{qs}"

    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {token}"},
    )

    for attempt in range(_MAX_RETRIES):
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = int(e.headers.get("Retry-After", "2"))
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(retry_after)
                    continue
                raise RuntimeError(f"Slack API rate limited after {_MAX_RETRIES} retries") from e
            body_text = e.read().decode(errors="replace")
            raise RuntimeError(f"Slack API error {e.code}: {body_text}") from e

        if not data.get("ok"):
            raise RuntimeError(f"Slack API error: {data.get('error', 'unknown')}")
        return data

    raise RuntimeError(f"Slack API failed after {_MAX_RETRIES} retries")


def fetch_channel_messages(
    token: str,
    channel_id: str,
    oldest_days: int = 90,
    max_count: Optional[int] = None,
) -> List[dict]:
    """Paginate conversations.history. Returns messages with reactions."""
    oldest_ts = str(int(time.time()) - oldest_days * 86400)
    messages = []
    cursor = None

    while True:
        params = {
            "channel": channel_id,
            "oldest": oldest_ts,
            "limit": "200",
        }
        if cursor:
            params["cursor"] = cursor

        data = _slack_request("conversations.history", token, params)

        for msg in data.get("messages", []):
            messages.append(msg)
            if max_count is not None and len(messages) >= max_count:
                return messages

        meta = data.get("response_metadata", {})
        cursor = meta.get("next_cursor")
        if not cursor:
            break

    return messages


def fetch_thread_replies(
    token: str,
    channel_id: str,
    thread_ts: str,
) -> List[dict]:
    """Fetch all replies for a thread via conversations.replies."""
    params = {
        "channel": channel_id,
        "ts": thread_ts,
        "limit": "200",
    }
    data = _slack_request("conversations.replies", token, params)
    replies = data.get("messages", [])
    # First message is the root; return only actual replies
    return [r for r in replies if r.get("ts") != thread_ts]


def resolve_channel_name(token: str, name: str) -> Optional[str]:
    """Find channel ID by name via conversations.list."""
    name_clean = name.lstrip("#").lower()
    cursor = None

    while True:
        params = {"limit": "200", "types": "public_channel"}
        if cursor:
            params["cursor"] = cursor

        data = _slack_request("conversations.list", token, params)

        for ch in data.get("channels", []):
            if ch.get("name", "").lower() == name_clean:
                return ch["id"]

        meta = data.get("response_metadata", {})
        cursor = meta.get("next_cursor")
        if not cursor:
            break

    return None


def fetch_user_display_name(
    token: str,
    user_id: str,
    cache: dict,
) -> str:
    """Resolve user ID to display name. Caches per import run."""
    if user_id in cache:
        return cache[user_id]

    try:
        data = _slack_request("users.info", token, {"user": user_id})
        user = data.get("user", {})
        profile = user.get("profile", {})
        name = (
            profile.get("display_name") or profile.get("real_name") or user.get("name") or user_id
        )
    except Exception:
        name = user_id

    cache[user_id] = name
    return name


# ---------------------------------------------------------------------------
# Reaction detection
# ---------------------------------------------------------------------------


def has_reaction(message: dict, reaction_name: str) -> bool:
    """Check if a message has a specific reaction."""
    for reaction in message.get("reactions", []):
        if reaction.get("name") == reaction_name:
            return True
    return False


def find_resolution_replies(
    replies: List[dict],
    resolution_reaction: str,
) -> List[dict]:
    """Return replies that have the resolution reaction, in chronological order."""
    return [r for r in replies if has_reaction(r, resolution_reaction)]


# ---------------------------------------------------------------------------
# Text processing
# ---------------------------------------------------------------------------

_URL_LINK_RE = re.compile(r"<(https?://[^|>]+)\|([^>]+)>")
_URL_BARE_RE = re.compile(r"<(https?://[^>]+)>")
_USER_MENTION_RE = re.compile(r"<@(U[A-Z0-9]+)>")
_CHANNEL_MENTION_RE = re.compile(r"<#C[A-Z0-9]+\|([^>]+)>")
_BOLD_RE = re.compile(r"\*([^*]+)\*")
_ITALIC_RE = re.compile(r"(?<!\w)_([^_]+)_(?!\w)")
_STRIKE_RE = re.compile(r"~([^~]+)~")
_CODE_BLOCK_RE = re.compile(r"```(.*?)```", re.DOTALL)


def _slack_mrkdwn_to_text(
    text: str,
    user_cache: dict,
    fetch_user_fn: Optional[Callable] = None,
) -> str:
    """Convert Slack mrkdwn to plain text."""
    if not text:
        return ""

    # URL links: <URL|text> → text
    text = _URL_LINK_RE.sub(r"\2", text)
    # Bare URLs: <URL> → URL
    text = _URL_BARE_RE.sub(r"\1", text)

    # User mentions: <@U123> → @display_name
    def _replace_user(match):
        uid = match.group(1)
        if fetch_user_fn:
            name = fetch_user_fn(uid, user_cache)
        else:
            name = user_cache.get(uid, uid)
        return f"@{name}"

    text = _USER_MENTION_RE.sub(_replace_user, text)

    # Channel mentions: <#C123|channel> → #channel
    text = _CHANNEL_MENTION_RE.sub(r"#\1", text)

    # Formatting: strip markers but keep content
    text = _BOLD_RE.sub(r"\1", text)
    text = _ITALIC_RE.sub(r"\1", text)
    text = _STRIKE_RE.sub(r"\1", text)

    return text.strip()


def _extract_code_blocks(text: str) -> List[str]:
    """Extract ```...``` code blocks from message text."""
    return [m.group(1).strip() for m in _CODE_BLOCK_RE.finditer(text) if m.group(1).strip()]


# ---------------------------------------------------------------------------
# Resolution formatting
# ---------------------------------------------------------------------------


def format_resolution(
    resolution_replies: List[dict],
    user_cache: dict,
    fetch_user_fn: Optional[Callable] = None,
) -> str:
    """Format resolution replies into structured text.
    Single reply: plain text. Multiple: numbered steps with author attribution."""
    if not resolution_replies:
        return ""

    if len(resolution_replies) == 1:
        reply = resolution_replies[0]
        return _slack_mrkdwn_to_text(reply.get("text", ""), user_cache, fetch_user_fn)

    parts = []
    for i, reply in enumerate(resolution_replies, 1):
        uid = reply.get("user", "unknown")
        if fetch_user_fn:
            author = fetch_user_fn(uid, user_cache)
        else:
            author = user_cache.get(uid, uid)
        text = _slack_mrkdwn_to_text(reply.get("text", ""), user_cache, fetch_user_fn)
        parts.append(f"[Step {i} — @{author}]\n{text}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------


def extract(
    threads: List[dict],
    extra_tags: List[str],
    max_count: Optional[int],
    channel_name: str,
    resolution_reaction: str,
    fetch_user_fn: Optional[Callable] = None,
) -> Tuple[List, int, int]:
    """
    Extract Fix objects from Slack threads.

    threads: [{root: msg_dict, replies: [msg_dict...], channel_id: str}, ...]
    Returns (fixes, skipped_no_resolution, bad_rows).
    """
    fixes = []
    skipped_no_resolution = 0
    bad_rows = 0
    user_cache = {}  # type: dict

    for thread in threads:
        if max_count is not None and len(fixes) >= max_count:
            break

        try:
            root = thread["root"]
            replies = thread.get("replies", [])
            channel_id = thread.get("channel_id", "")

            # 1. Root message text → issue
            root_text = _slack_mrkdwn_to_text(root.get("text", ""), user_cache, fetch_user_fn)

            # 2. Code blocks from root → error_excerpt
            code_blocks = _extract_code_blocks(root.get("text", ""))
            error_excerpt = "\n\n".join(code_blocks) if code_blocks else None

            # 3. Find resolution replies
            res_replies = find_resolution_replies(replies, resolution_reaction)
            if not res_replies:
                skipped_no_resolution += 1
                continue

            # 4. Format resolution
            resolution = format_resolution(res_replies, user_cache, fetch_user_fn)

            # 5. Auto-detect tags
            combined = root_text + " " + resolution
            if error_excerpt:
                combined += " " + error_excerpt
            resource_types, kw_tags = detect_resource_types(combined)

            # 6. Source tag
            thread_ts = root.get("ts", "")
            source_tag = f"source:slack:{channel_id}_{thread_ts}"

            tags_str = normalize_tags(
                resource_types,
                kw_tags,
                source_tag,
                [t.lower() for t in extra_tags],
            )

            # 7. Resolve authors
            root_author = "unknown"
            if fetch_user_fn and root.get("user"):
                root_author = fetch_user_fn(root["user"], user_cache)
            elif root.get("user"):
                root_author = user_cache.get(root["user"], root["user"])

            resolver_uids = list(dict.fromkeys(r.get("user", "unknown") for r in res_replies))
            resolver_names = []
            for uid in resolver_uids:
                if fetch_user_fn:
                    resolver_names.append(fetch_user_fn(uid, user_cache))
                else:
                    resolver_names.append(user_cache.get(uid, uid))

            # 8. Build notes
            ts_date = ""
            try:
                ts_float = float(root.get("ts", "0"))
                import datetime

                ts_date = datetime.datetime.fromtimestamp(
                    ts_float, tz=datetime.timezone.utc
                ).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                pass

            thread_link = ""
            if channel_id and thread_ts:
                ts_clean = thread_ts.replace(".", "")
                thread_link = f"https://slack.com/archives/{channel_id}/p{ts_clean}"

            notes_parts = [f"Source: slack / #{channel_name}"]
            if thread_link:
                notes_parts.append(f"Thread: {thread_link}")
            if root_author and ts_date:
                notes_parts.append(f"Posted by: {root_author} ({ts_date})")
            elif root_author:
                notes_parts.append(f"Posted by: {root_author}")
            if resolver_names:
                notes_parts.append(f"Resolved by: {', '.join(resolver_names)}")

            notes = "\n".join(notes_parts)

            # 9. Build fix
            fix = build_fix(
                issue=root_text,
                resolution=resolution,
                error_excerpt=error_excerpt,
                tags=tags_str,
                notes=notes,
            )
            fixes.append(fix)

        except Exception:
            bad_rows += 1

    return fixes, skipped_no_resolution, bad_rows
