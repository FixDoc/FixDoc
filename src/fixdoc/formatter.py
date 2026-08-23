"""Markdown formatting for fixes."""

from typing import Optional

from .models import Fix


def _extract_source_line(fix: Fix) -> Optional[str]:
    """Return 'system / id' string if a source:system:id tag exists, else None."""
    if not fix.tags:
        return None
    for tag in fix.tags.split(","):
        tag = tag.strip()
        if tag.startswith("source:"):
            parts = tag.split(":", 2)
            if len(parts) == 3:
                return f"{parts[1]} / {parts[2]}"
    return None


def fix_to_markdown(fix: Fix) -> str:
    """Generate markdown documentation for a fix."""
    lines = [
        f"# Fix: {fix.id[:8]}",
        "",
        f"**Created:** {fix.created_at}",
        "",
        f"**Updated:** {fix.updated_at}",
        "",
    ]

    if fix.author:
        lines.append(f"**Author:** {fix.author}")
    if fix.author_email:
        lines.append(f"**Author Email:** {fix.author_email}")

    lines.append("")

    if fix.author:
        lines.append(f"**Author:** {fix.author}")
    if fix.author_email:
        lines.append(f"**Author Email:** {fix.author_email}")

    lines.append("")

    if fix.tags:
        lines.extend([f"**Tags:** `{fix.tags}`", ""])

    source_line = _extract_source_line(fix)
    if source_line:
        lines.extend([f"**Source:** {source_line}", ""])

    if fix.memory_type and fix.memory_type != "fix":
        lines.extend([f"**Memory Type:** {fix.memory_type}", ""])

    lines.extend(
        [
            "## Issue",
            "",
            fix.issue,
            "",
            "## Resolution",
            "",
            fix.resolution,
            "",
        ]
    )

    if fix.error_excerpt:
        lines.extend(
            [
                "## Error Excerpt",
                "",
                "```",
                fix.error_excerpt,
                "```",
                "",
            ]
        )

    if fix.notes:
        lines.extend(
            [
                "## Notes",
                "",
                fix.notes,
                "",
            ]
        )

    if fix.source_error_ids:
        lines.extend(
            [
                "## Source Error IDs",
                "",
                ", ".join(fix.source_error_ids),
                "",
            ]
        )

    if fix.applied_count > 0:
        rate = fix.effectiveness_rate
        rate_str = f"{rate:.0%}" if rate is not None else "N/A"
        lines.extend(
            [
                "## Effectiveness",
                "",
                f"Applied: {fix.applied_count} | "
                f"Successful: {fix.success_count} | "
                f"Rate: {rate_str}",
                "",
            ]
        )

    return "\n".join(lines)
