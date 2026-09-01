"""Slack Web API plumbing, resurrected from the legacy importer.

Read-only calls with 429 retry, cursor pagination, channel-name resolution,
per-run user-name caching, and mrkdwn cleanup. The emoji-convention layer the
legacy importer built on top did not survive review ("people don't care to use
emojis"); extraction is now the model's job (slack_extract.py).
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional


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
