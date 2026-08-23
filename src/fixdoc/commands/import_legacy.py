"""fixdoc import-legacy — bring an old-CLI fixes.json into the knowledge store.

The cold-start command: fixdoc <= 0.0.4 stored one-liner issue/resolution
pairs in ~/.fixdoc/fixes.json. This maps them into spec entries, honestly:
what the legacy format never captured (root cause, verification) becomes an
explicit "(add during review)" placeholder rather than invented content, and
everything lands quarantined unless the human running this vouches with
--trust. Entry ids derive from the legacy content_hash, so re-running the
import never duplicates.
"""

import hashlib
import json
import re
from pathlib import Path

import click

from fixdoc.core.models import Entry

PLACEHOLDER = "(not captured in the legacy record — add during review)"


def _content_hash(fix: dict) -> str:
    """The old tool's formula (sha256 of normalized issue+resolution), replicated
    for records that predate the content_hash field — same input, same id."""
    if fix.get("content_hash"):
        return fix["content_hash"]
    normalized = " ".join(
        re.sub(r"\s+", " ", (fix.get(k) or "").strip().lower()) for k in ("issue", "resolution")
    )
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _legacy_to_entry(fix: dict, status: str) -> Entry:
    issue = (fix.get("issue") or "").strip()
    resolution = (fix.get("resolution") or "").strip()
    # check folds into playbook per the spec; everything else imports as fix.
    entry_type = "playbook" if fix.get("memory_type") == "check" else "fix"
    prefix = "pb" if entry_type == "playbook" else "fx"
    if entry_type == "playbook":
        sections = {"When to use": issue, "Steps": resolution, "Verification": PLACEHOLDER}
    else:
        sections = {
            "Symptom": issue,
            "Root cause": PLACEHOLDER,
            "Fix": resolution,
            "Verification": PLACEHOLDER,
        }
    notes = " | ".join(
        part
        for part in [(fix.get("notes") or "").strip(), f"legacy id: {fix.get('id', '')}"]
        if part
    )
    if fix.get("tags"):
        notes += " | tags: " + ", ".join(fix["tags"])
    sections["Notes"] = notes
    title = issue if len(issue) <= 90 else issue[:87] + "..."
    return Entry(
        # Deterministic id from the legacy content hash: same input, same file,
        # so re-imports are no-ops instead of duplicates.
        id=f"{prefix}_{_content_hash(fix)[:8]}",
        type=entry_type,
        title=title,
        sections=sections,
        status=status,
        occurrences=fix.get("success_count") or 0,
        created=(fix.get("created_at") or "")[:10],
    )


@click.command("import-legacy")
@click.option(
    "--source",
    default=str(Path.home() / ".fixdoc" / "fixes.json"),
    show_default=True,
    help="Path to the old fixdoc fixes.json.",
)
@click.option(
    "--store",
    "store_dir",
    default=".",
    help="Repo root containing knowledge/ (default: current directory).",
)
@click.option("--namespace", default="shared", show_default=True)
@click.option(
    "--trust",
    is_flag=True,
    help="Import as validated instead of quarantined. You are the "
    "reviewer: only use this for fixes you personally vouch for.",
)
def import_legacy(source, store_dir, namespace, trust):
    """Import an old fixdoc (<= 0.0.4) fixes.json into the knowledge store."""
    data = json.loads(Path(source).read_text())
    fixes = data if isinstance(data, list) else data.get("fixes", [])
    target = Path(store_dir) / "knowledge" / namespace
    target.mkdir(parents=True, exist_ok=True)
    status = "validated" if trust else "quarantined"

    imported, skipped_existing, skipped_private = 0, 0, 0
    for fix in fixes:
        if fix.get("is_private"):
            skipped_private += 1  # private stays private: never leaves fixes.json
            continue
        entry = _legacy_to_entry(fix, status)
        path = target / f"{entry.id}.md"
        if path.exists():
            skipped_existing += 1
            continue
        path.write_text(entry.to_markdown())
        imported += 1

    click.echo(
        f"{imported} imported as {status}, "
        f"{skipped_existing} duplicate or already present, {skipped_private} private (skipped)"
    )
    if imported and not trust:
        click.echo(
            "Review each entry (fill the placeholders, then set "
            "status: validated) to make it retrievable — or re-run with "
            "--trust if you vouch for the batch."
        )
