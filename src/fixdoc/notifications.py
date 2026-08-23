"""Slack notifications for fixdoc watch.

Posts a summary to Slack when errors match known fixes.
Uses urllib (no extra dependencies).
"""

import json
import time
import urllib.error
import urllib.request

from .rendering import format_suggestion_preview

_SLACK_API = "https://slack.com/api"
_MAX_RETRIES = 3


def _slack_post(endpoint, token, payload):
    """POST to a Slack API endpoint. Retries on 429. Returns response dict."""
    url = f"{_SLACK_API}/{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )

    for attempt in range(_MAX_RETRIES):
        try:
            with urllib.request.urlopen(req) as resp:
                result = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = int(e.headers.get("Retry-After", "2"))
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(retry_after)
                    continue
                return {"ok": False, "error": "rate_limited"}
            return {"ok": False, "error": f"http_{e.code}"}

        return result

    return {"ok": False, "error": "max_retries"}


def _build_blocks(entries, suggestions, cwd=None, command=None):
    """Build Slack Block Kit blocks for the notification message.

    Args:
        entries: list of PendingEntry
        suggestions: list of (entry_label, fix) tuples
        cwd: working directory (optional)
        command: the command that failed (optional)
    """
    blocks = []

    n = len(entries)
    header_text = f":warning: *FixDoc: {n} error(s) detected*"
    if command:
        header_text += f"\nCommand: `{command}`"
    if cwd:
        header_text += f"\nDirectory: `{cwd}`"

    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": header_text},
        }
    )

    blocks.append({"type": "divider"})

    error_lines = []
    for i, entry in enumerate(entries[:5], 1):
        resource = entry.resource_address or entry.short_message[:60]
        code = f" `{entry.error_code}`" if entry.error_code else ""
        error_lines.append(f"{i}. {resource}{code}")
    if len(entries) > 5:
        error_lines.append(f"... and {len(entries) - 5} more")

    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(error_lines)},
        }
    )

    if suggestions:
        blocks.append({"type": "divider"})
        fix_lines = [":bulb: *Known fixes that may help:*"]
        for entry_label, fix in suggestions[:3]:
            res_preview = format_suggestion_preview(fix, max_len=80)
            fix_lines.append(f"  `{fix.id[:8]}`: {res_preview}")
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(fix_lines)},
            }
        )

    return blocks


def post_slack_notification(
    token,
    channel,
    entries,
    suggestions,
    cwd=None,
    command=None,
):
    """Post a notification to Slack. Returns True on success.

    Args:
        token: Slack bot token
        channel: Channel ID or name
        entries: list of PendingEntry
        suggestions: list of (entry_label, fix) tuples
        cwd: working directory
        command: the command that failed
    """
    blocks = _build_blocks(entries, suggestions, cwd=cwd, command=command)

    n = len(entries)
    fallback = f"FixDoc: {n} error(s) detected"

    payload = {
        "channel": channel,
        "text": fallback,
        "blocks": blocks,
    }

    result = _slack_post("chat.postMessage", token, payload)
    return result.get("ok", False)
