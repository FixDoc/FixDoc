"""AI-powered error diagnosis for fixdoc.

Provides error explanation and fix suggestions using Claude API.
Follows the same lazy-import pattern as analyze.py.
"""

import os
from typing import Optional

from .pending import PendingEntry


def diagnose_error(
    entry: PendingEntry,
    api_key: Optional[str] = None,
    model: str = "claude-haiku-4-5-20251001",
) -> Optional[str]:
    """Call Claude API to explain an error and suggest a fix.

    Returns diagnosis text, or None if anthropic is not installed
    or the call fails.
    """
    try:
        import anthropic
    except ImportError:
        return None

    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    prompt_parts = [
        "You are an infrastructure engineer's assistant." " A command failed with this error.",
        "",
        f"Error type: {entry.error_type}",
    ]
    if entry.resource_address:
        prompt_parts.append(f"Resource: {entry.resource_address}")
    if entry.error_code:
        prompt_parts.append(f"Error code: {entry.error_code}")
    if entry.command:
        prompt_parts.append(f"Command: {entry.command}")

    prompt_parts.append("")
    prompt_parts.append("Error output:")
    excerpt = (entry.error_excerpt or entry.short_message)[:1500]
    prompt_parts.append(excerpt)
    prompt_parts.append("")
    prompt_parts.append(
        "In 2-4 concise bullet points, explain:\n"
        "1. WHY this error occurred (root cause)\n"
        "2. What to try to fix it (actionable steps)\n"
        "Use plain text with bullet markers. No headers or intro."
    )

    prompt = "\n".join(prompt_parts)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except Exception:
        return None


def diagnose_errors(
    entries: list,
    api_key: Optional[str] = None,
    max_errors: int = 3,
    model: str = "claude-haiku-4-5-20251001",
) -> list:
    """Diagnose up to max_errors entries.

    Returns list of (entry, diagnosis_text) tuples.
    Entries without diagnosis (API failure) are skipped.
    """
    results = []
    for entry in entries[:max_errors]:
        diagnosis = diagnose_error(entry, api_key=api_key, model=model)
        if diagnosis:
            results.append((entry, diagnosis))
    return results
