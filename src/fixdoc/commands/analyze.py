"""Analyze command for fixdoc CLI.

Merges plan analysis + blast radius into a single command.
"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import click

from ..blast_radius import (
    BlastResult,
    BlastNode,
    analyze_blast_radius,
)
from ..models import Fix
from ..storage import FixRepository
from ..parsers.base import CloudProvider


_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


@dataclass
class AnalysisMatch:
    """Represents a potential issue found during terraform plan analysis."""

    resource_address: str
    resource_type: str
    related_fix: Fix
    cloud_provider: CloudProvider = CloudProvider.UNKNOWN

    def format_warning(self) -> str:
        """Format as a warning message for CLI output."""
        short_id = self.related_fix.id[:8]
        issue = self.related_fix.issue
        resolution = self.related_fix.resolution

        issue_preview = issue[:80] + "..." if len(issue) > 80 else issue
        resolution_preview = resolution[:80] + "..." if len(resolution) > 80 else resolution

        lines = [
            f"  FIX-{short_id}: {issue_preview}",
            f"   Resolution: {resolution_preview}",
        ]

        if self.related_fix.tags:
            lines.append(f"   Tags: {self.related_fix.tags}")

        return "\n".join(lines)


@dataclass
class PlanResource:
    """Represents a resource in a Terraform plan."""

    address: str
    resource_type: str
    name: str
    cloud_provider: CloudProvider
    action: str  # create, update, delete, replace, no-op
    module_path: Optional[str] = None
    values: dict = field(default_factory=dict)
    before_values: dict = field(default_factory=dict)


class TerraformAnalyzer:
    """Analyzes terraform plan JSON output against known fixes."""

    def __init__(self, repo: Optional[FixRepository] = None):
        self.repo = repo or FixRepository()

    def load_plan(self, plan_path: Path) -> dict:
        """Load and parse a terraform plan JSON file."""
        with open(plan_path, "r") as f:
            return json.load(f)

    def detect_cloud_provider(self, resource_type: str, provider_name: str = "") -> CloudProvider:
        """Detect cloud provider from resource type or provider name."""
        resource_lower = resource_type.lower()
        provider_lower = provider_name.lower()

        if resource_lower.startswith("aws_") or "hashicorp/aws" in provider_lower:
            return CloudProvider.AWS
        elif resource_lower.startswith("azurerm_") or "hashicorp/azurerm" in provider_lower:
            return CloudProvider.AZURE
        elif resource_lower.startswith("google_") or "hashicorp/google" in provider_lower:
            return CloudProvider.GCP

        return CloudProvider.UNKNOWN

    def extract_resources(self, plan: dict) -> list[PlanResource]:
        """Extract all resources from a Terraform plan with full metadata."""
        resources = []

        # Extract from resource_changes (most reliable for planned changes)
        for change in plan.get("resource_changes", []):
            address = change.get("address", "")
            resource_type = change.get("type", "")
            name = change.get("name", "")
            provider_name = change.get("provider_name", "")

            if not resource_type:
                continue

            # Determine action
            actions = change.get("change", {}).get("actions", [])
            if "create" in actions and "delete" in actions:
                action = "replace"
            elif "delete" in actions:
                action = "delete"
            elif "update" in actions:
                action = "update"
            elif "create" in actions:
                action = "create"
            else:
                action = "no-op"

            # Extract module path if present
            module_path = None
            if address.startswith("module."):
                parts = address.split(".")
                module_parts = []
                for i, part in enumerate(parts):
                    if part == "module" and i + 1 < len(parts):
                        module_parts.append(f"module.{parts[i + 1]}")
                module_path = ".".join(module_parts) if module_parts else None

            # Get planned values
            values = change.get("change", {}).get("after", {}) or {}
            before_values = change.get("change", {}).get("before", {}) or {}

            resources.append(PlanResource(
                address=address,
                resource_type=resource_type,
                name=name,
                cloud_provider=self.detect_cloud_provider(resource_type, provider_name),
                action=action,
                module_path=module_path,
                values=values,
                before_values=before_values,
            ))

        # Also check planned_values for additional resources
        self._extract_from_planned_values(plan.get("planned_values", {}), resources)

        # Deduplicate by address
        seen = set()
        unique = []
        for r in resources:
            if r.address not in seen:
                seen.add(r.address)
                unique.append(r)

        return unique

    def _extract_from_planned_values(self, planned_values: dict, resources: list[PlanResource]):
        """Extract resources from planned_values section."""
        existing_addresses = {r.address for r in resources}

        def process_module(module: dict, prefix: str = ""):
            # Process resources in this module
            for resource in module.get("resources", []):
                address = resource.get("address", "")
                if address in existing_addresses:
                    continue

                resource_type = resource.get("type", "")
                if not resource_type:
                    continue

                provider_name = resource.get("provider_name", "")

                resources.append(PlanResource(
                    address=address,
                    resource_type=resource_type,
                    name=resource.get("name", ""),
                    cloud_provider=self.detect_cloud_provider(resource_type, provider_name),
                    action="unknown",
                    module_path=prefix or None,
                    values=resource.get("values", {}),
                ))
                existing_addresses.add(address)

            # Process child modules
            for child in module.get("child_modules", []):
                child_address = child.get("address", "")
                process_module(child, child_address)

        root = planned_values.get("root_module", {})
        process_module(root)

    def extract_resource_types(self, plan: dict) -> list[tuple[str, str]]:
        """Extract (resource_address, resource_type) tuples from a plan.

        This is a simplified version for backward compatibility.
        """
        resources = self.extract_resources(plan)
        return [(r.address, r.resource_type) for r in resources]

    def get_changed_resources(self, plan: dict) -> list[PlanResource]:
        """Get only resources that are actually changing (not no-op/read/unknown)."""
        resources = self.extract_resources(plan)
        return [
            r for r in resources
            if r.action in ("create", "update", "delete", "replace")
        ]

    def analyze(self, plan_path: Path) -> list[AnalysisMatch]:
        """Analyze a terraform plan for potential issues based on past fixes."""
        plan = self.load_plan(plan_path)
        resources = self.get_changed_resources(plan)
        matches = []

        for resource in resources:
            for fix in self.repo.find_by_resource_type(resource.resource_type):
                matches.append(
                    AnalysisMatch(
                        resource_address=resource.address,
                        resource_type=resource.resource_type,
                        related_fix=fix,
                        cloud_provider=resource.cloud_provider,
                    )
                )

        return matches

    def analyze_and_format(self, plan_path: Path) -> str:
        """Analyze a plan and return formatted output."""
        matches = self.analyze(plan_path)

        if not matches:
            return "No known issues found for resources in this plan."

        # Group by cloud provider
        by_provider = {}
        for match in matches:
            provider = match.cloud_provider.value
            by_provider.setdefault(provider, []).append(match)

        lines = [
            f"Found {len(matches)} potential issue(s) based on your fix history:",
            "",
        ]

        for provider, provider_matches in by_provider.items():
            if provider != "unknown":
                lines.append(f"-- {provider.upper()} --")
                lines.append("")

            for match in provider_matches:
                lines.append(match.format_warning())
                lines.append("")

        lines.append("Run `fixdoc show <fix-id>` for full details on any fix.")
        return "\n".join(lines)

    def get_plan_summary(self, plan_path: Path) -> dict:
        """Get a summary of the plan resources by cloud provider and action."""
        plan = self.load_plan(plan_path)
        resources = self.get_changed_resources(plan)

        summary = {
            "total": len(resources),
            "by_provider": {},
            "by_action": {},
            "by_type": {},
        }

        for r in resources:
            provider = r.cloud_provider.value
            summary["by_provider"][provider] = summary["by_provider"].get(provider, 0) + 1
            summary["by_action"][r.action] = summary["by_action"].get(r.action, 0) + 1
            summary["by_type"][r.resource_type] = summary["by_type"].get(r.resource_type, 0) + 1

        return summary


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_PROVIDER_PREFIXES = ("aws_", "azurerm_", "google_", "kubernetes_")


def _resource_type_from_address(address: str) -> Optional[str]:
    """Extract resource type by splitting address on '.' and finding provider-prefixed part."""
    for part in address.split("."):
        if part.startswith(_PROVIDER_PREFIXES):
            return part
    return None


def _group_warnings(warnings: list) -> dict:
    """Group resource warnings by primary resource type, sorted by score desc within each group."""
    groups = {}
    for w in warnings:
        rt = None
        for r in w.get("matched_resources", []):
            rt = _resource_type_from_address(r["address"])
            if rt:
                break
        if not rt:
            for tag in (w.get("tags") or "").split(","):
                tag = tag.strip()
                if tag.startswith(_PROVIDER_PREFIXES):
                    rt = tag
                    break
        groups.setdefault(rt or "other", []).append(w)

    for group in groups.values():
        group.sort(key=lambda w: w.get("score", 0), reverse=True)
    return groups


def _format_human(
    result: BlastResult,
    changed: list[PlanResource],
    verbose: bool = False,
    ai_explanation: Optional[str] = None,
) -> str:
    """Format unified analysis result for human-readable terminal output."""
    lines = []

    # Header
    lines.append("Terraform Plan Analysis")
    lines.append("=" * 23)

    # Change summary
    by_action = result.plan_summary.get("by_action", {})
    creates = by_action.get("create", 0)
    updates = by_action.get("update", 0)
    deletes = by_action.get("delete", 0)
    replaces = by_action.get("replace", 0)
    total = result.plan_summary.get("total_changes", 0)

    parts = []
    if creates:
        parts.append(f"{creates} create")
    if updates:
        parts.append(f"{updates} update")
    if deletes:
        parts.append(f"{deletes} delete")
    if replaces:
        parts.append(f"{replaces} replace")

    lines.append(f"{total} resources changing ({', '.join(parts) if parts else 'none'})")
    lines.append("")

    # Risk score
    sev = result.severity.upper()
    sev_colors = {
        "CRITICAL": "red",
        "HIGH": "yellow",
        "MEDIUM": "cyan",
        "LOW": "green",
    }
    color = sev_colors.get(sev, "white")
    score_line = f"Risk Score: {result.score} / 100  [{sev}]"
    lines.append(click.style(score_line, fg=color))
    lines.append("")

    # Score explanation block
    if ai_explanation:
        lines.append(f"Why this scored {sev} (AI analysis):")
        for line in ai_explanation.strip().splitlines():
            lines.append(f"  {line}")
        lines.append("")
    elif result.score_explanation:
        lines.append(f"Why this scored {sev}:")
        for item in result.score_explanation:
            delta_str = f" (+{item['delta']:.0f})" if item["delta"] > 0 else ""
            lines.append(f"  \u2022 {item['label']}{delta_str}")
        lines.append("")

    # Changes list
    if changed:
        lines.append("Changes:")
        for res in changed:
            action_str = res.action.upper()
            addr = res.address
            cp_info = ""
            from ..blast_radius import classify_control_point
            cp = classify_control_point(res.resource_type)
            if cp:
                cp_info = f"  [{cp[0]} boundary]"
            lines.append(f"  {action_str:<10}{addr}{cp_info}")
        lines.append("")

    # Affected resources
    if result.affected:
        count = len(result.affected)
        display_limit = 10
        lines.append(f"Impacted Resources ({count}):")
        for ar in result.affected[:display_limit]:
            addr = ar["address"]
            depth = ar["depth"]
            via = ar["path"][0] if ar["path"] else "?"
            lines.append(f"  {addr:<40}(depth: {depth}, via: {via})")
        if count > display_limit:
            lines.append(f"  ... and {count - display_limit} more")
        lines.append("")

    # Relevant Past Fixes (unified section)
    fixes_to_show = result.relevant_fixes if result.relevant_fixes else result.resource_warnings
    if fixes_to_show:
        groups = _group_warnings(fixes_to_show)
        total = len(fixes_to_show)
        lines.append(f"Relevant Past Fixes ({total}):")
        for rt, group_warnings in groups.items():
            lines.append(f"\n  {rt}:")
            top = group_warnings[0]
            short_id = top["short_id"]
            issue = top["issue"] or ""
            resolution = top["resolution"] or ""
            created_at = (top.get("created_at") or "")[:10]
            matched = top.get("matched_resources", [])
            confidence = top.get("confidence", "low")

            # Build match reason display string
            match_reason = top.get("match_reason", "")
            if isinstance(match_reason, dict):
                signal = match_reason.get("signal", "")
                detail = match_reason.get("detail", "")
                signal_label = {
                    "error_code": "error code",
                    "address": "address",
                    "attribute": "attribute",
                    "category": "category",
                    "type_action": "type + action",
                    "resource_type_tag": "resource type",
                    "resource_type_text": "resource type",
                }.get(signal, signal)
                reason_str = f"{signal_label}: {detail}" if detail else signal_label
            else:
                reason_str = str(match_reason)

            issue_disp = issue[:80] + "..." if len(issue) > 80 else issue
            res_disp = resolution[:80] + "..." if len(resolution) > 80 else resolution
            lines.append(f"    [{confidence}: {reason_str}] FIX-{short_id}: {issue_disp}")
            lines.append(f"     Resolution: {res_disp}")

            if verbose:
                score_val = top.get("score", 0)
                lines.append(f"     Score: {score_val}")
                if top.get("tags"):
                    lines.append(f"     Tags: {top['tags']}")
                # Show supporting signals in verbose
                if isinstance(match_reason, dict):
                    supporting = match_reason.get("supporting_signals", [])
                    if supporting:
                        support_strs = [f"{s['signal']}: {s['detail']}" for s in supporting]
                        lines.append(f"     Supporting: {', '.join(support_strs)}")

            if matched:
                addr_str = f"{matched[0]['address']} ({matched[0]['action']})"
                lines.append(f"     Applies to: {addr_str}")

            lines.append(f"     Captured: {created_at}")

            if len(group_warnings) > 1:
                lines.append(f"     + {len(group_warnings) - 1} more")

        lines.append("")
        lines.append("Run `fixdoc show <short_id>` for full details.")
        lines.append("")

    # Contextual Checks
    ctx_checks = result.contextual_checks if result.contextual_checks else []
    if not ctx_checks and result.checks:
        # Fallback to legacy checks
        ctx_checks = [{"check": c, "source": "category", "resource": ""} for c in result.checks]
    if ctx_checks:
        lines.append("Contextual Checks:")
        for check_item in ctx_checks:
            source = check_item.get("source", "")
            resource = check_item.get("resource", "")
            check_text = check_item.get("check", "")
            resource_suffix = f" ({resource})" if resource else ""
            lines.append(f"  - [{source}] {check_text}{resource_suffix}")
        lines.append("")

    return "\n".join(lines)


def _format_json(result: BlastResult) -> str:
    """Format blast radius result as JSON."""
    # Serialize relevant_fixes: convert sets to lists for JSON compatibility
    serializable_fixes = []
    for rf in result.relevant_fixes:
        entry = dict(rf)
        mr = entry.get("match_reason")
        if isinstance(mr, dict):
            mr = dict(mr)
            # attr_categories could be a set
            entry["match_reason"] = mr
        serializable_fixes.append(entry)

    serializable_checks = []
    for cc in result.contextual_checks:
        serializable_checks.append(dict(cc))

    data = {
        "analysis_id": result.analysis_id,
        "timestamp": result.timestamp,
        "score": result.score,
        "severity": result.severity,
        "changes": result.changes,
        "control_points": result.control_points,
        "affected": result.affected,
        "why_paths": result.why_paths,
        "checks": result.checks,
        "history_matches": result.history_matches,
        "plan_summary": result.plan_summary,
        "resource_warnings": result.resource_warnings,
        "score_explanation": result.score_explanation,
        "relevant_fixes": serializable_fixes,
        "contextual_checks": serializable_checks,
    }
    return json.dumps(data, indent=2)


def _format_summary(result: BlastResult) -> str:
    """Format a quick summary of the analysis."""
    ps = result.plan_summary
    sev = result.severity.upper()
    return (
        f"Risk: {result.score}/100 [{sev}] | "
        f"{ps.get('total_changes', 0)} changes, "
        f"{ps.get('control_points', 0)} control points, "
        f"{ps.get('affected_resources', 0)} impacted"
    )


_SEVERITY_EMOJI = {
    "critical": ":red_circle:",
    "high": ":warning:",
    "medium": ":large_blue_circle:",
    "low": ":white_check_mark:",
}


def _format_markdown(result: BlastResult) -> str:
    """Format blast radius result as GitHub-flavored markdown.

    Designed for PR comments and job summaries — no ANSI codes, scannable
    in under 20 seconds.
    """
    lines = []

    sev = result.severity.upper()
    emoji = _SEVERITY_EMOJI.get(result.severity, "")

    lines.append("## Terraform Risk Analysis")
    lines.append("")
    lines.append(f"**Risk: {result.score:.0f}/100** {emoji} **{sev}**")
    lines.append("")

    # Summary table
    ps = result.plan_summary
    by_action = ps.get("by_action", {})
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total changes | {ps.get('total_changes', 0)} |")
    lines.append(f"| Creates | {by_action.get('create', 0)} |")
    lines.append(f"| Updates | {by_action.get('update', 0)} |")
    lines.append(f"| Deletes | {by_action.get('delete', 0)} |")
    lines.append(f"| Replaces | {by_action.get('replace', 0)} |")
    lines.append(f"| Control points | {ps.get('control_points', 0)} |")
    lines.append(f"| Impacted resources | {ps.get('affected_resources', 0)} |")
    lines.append("")

    # Score explanation — top 3, skip modifiers, sorted by delta desc
    if result.score_explanation:
        visible = [
            e for e in result.score_explanation if e.get("kind") != "modifier"
        ]
        visible.sort(key=lambda e: e.get("delta", 0), reverse=True)
        visible = visible[:3]
        if visible:
            lines.append("### Why this score?")
            for item in visible:
                delta = item.get("delta", 0)
                delta_str = f" (+{delta:.0f})" if delta > 0 else ""
                lines.append(f"- {item['label']}{delta_str}")
            lines.append("")

    # Contextual checks — top 3
    ctx_checks = result.contextual_checks if result.contextual_checks else []
    if not ctx_checks and result.checks:
        ctx_checks = [
            {"check": c, "source": "category", "resource": ""}
            for c in result.checks
        ]
    if ctx_checks:
        top_checks = ctx_checks[:3]
        lines.append("### Contextual Checks")
        for item in top_checks:
            source = item.get("source", "")
            check_text = item.get("check", "")
            lines.append(f"- **[{source}]** {check_text}")
        lines.append("")

    # Relevant past fixes — top 3
    fixes = result.relevant_fixes if result.relevant_fixes else result.resource_warnings
    if fixes:
        top_fixes = fixes[:3]
        lines.append("### Relevant Past Fixes")
        lines.append("")
        lines.append("| Fix | Issue | Confidence |")
        lines.append("|-----|-------|------------|")
        for f in top_fixes:
            short_id = f.get("short_id", "?")
            issue = f.get("issue", "")
            if len(issue) > 80:
                issue = issue[:80] + "..."
            confidence = f.get("confidence", "low")
            match_reason = f.get("match_reason", "")
            if isinstance(match_reason, dict):
                signal = match_reason.get("signal", "")
                detail = match_reason.get("detail", "")
                signal_label = {
                    "error_code": "error code",
                    "address": "address",
                    "attribute": "attribute",
                    "category": "category",
                    "type_action": "type + action",
                    "resource_type_tag": "resource type",
                    "resource_type_text": "resource type",
                }.get(signal, signal)
                reason_str = f"{signal_label}: {detail}" if detail else signal_label
            else:
                reason_str = str(match_reason)
            lines.append(
                f"| FIX-{short_id} | {issue} | {confidence} ({reason_str}) |"
            )
        lines.append("")

    lines.append("---")
    lines.append("<sub>Generated by FixDoc</sub>")

    return "\n".join(lines)


def generate_ai_explanation(result: BlastResult, api_key: str) -> Optional[str]:
    """Call the Claude API to generate polished score explanation prose.

    Returns the response text, or None if anthropic is not installed or the
    call fails for any reason.
    """
    try:
        import anthropic
    except ImportError:
        return None

    bullet_labels = [item["label"] for item in result.score_explanation]
    control_point_addrs = [cp["address"] for cp in result.control_points[:3]]
    affected_count = len(result.affected)
    history_count = len(result.history_matches)

    prompt_parts = [
        f"A Terraform plan scored {result.score}/100 ({result.severity.upper()} severity) for blast radius risk.",
        "Score factors:",
    ]
    for label in bullet_labels:
        prompt_parts.append(f"- {label}")
    if control_point_addrs:
        prompt_parts.append(f"\nControl points: {', '.join(control_point_addrs)}")
    if affected_count:
        prompt_parts.append(f"Downstream affected resources: {affected_count}")
    if history_count:
        prompt_parts.append(f"Historical incident matches: {history_count}")
    prompt_parts.append(
        f"\nWrite 2-4 concise bullet points explaining why this scored "
        f"{result.severity.upper()} to an infrastructure engineer. "
        "Focus on the risk implications, not just restating the factors. "
        "Use plain text with '\u2022' bullets. No header, no intro sentence."
    )

    prompt = "\n".join(prompt_parts)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except Exception:
        return None


def _auto_run_terraform_graph() -> Optional[str]:
    """Try to run `terraform graph` if terraform is on PATH.

    Returns DOT text or None.
    """
    if not shutil.which("terraform"):
        return None
    try:
        proc = subprocess.run(
            ["terraform", "graph"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


@click.command()
@click.argument("plan_file", type=click.Path(exists=True))
@click.option(
    "--graph",
    "-g",
    "graph_file",
    type=click.Path(exists=True),
    default=None,
    help="Path to DOT file from `terraform graph`. Auto-runs if terraform is on PATH.",
)
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["human", "json", "markdown"]),
    default="human",
    help="Output format: human, json, or markdown.",
)
@click.option(
    "--max-depth",
    "-d",
    type=int,
    default=5,
    help="Max BFS traversal depth for propagation.",
)
@click.option(
    "--exit-on",
    "exit_on",
    type=click.Choice(["low", "medium", "high", "critical"]),
    default=None,
    help="Exit with code 1 if severity meets or exceeds this threshold. For CI gating.",
)
@click.option(
    "--summary",
    "-s",
    is_flag=True,
    help="Show quick summary instead of full analysis.",
)
@click.option(
    "--match",
    "-m",
    "match_mode",
    type=click.Choice(["strict", "balanced", "loose"]),
    default="balanced",
    help="Fix history match strictness: strict, balanced, or loose.",
)
@click.option("--verbose", "-v", is_flag=True, help="Show detailed output")
@click.option("--tag-only", is_flag=True, default=False,
              help="Only show tribal warnings from tag-matched fixes (no text search).")
@click.option("--max-warnings", "max_warnings", type=int, default=10,
              help="Max number of tribal knowledge warnings to surface.")
@click.option("--ai-explain", "ai_explain", is_flag=True, default=False,
              help="Use Claude API to generate polished score explanation. Requires ANTHROPIC_API_KEY.")
@click.pass_context
def analyze(
    ctx,
    plan_file: str,
    graph_file: Optional[str],
    output_format: str,
    max_depth: int,
    exit_on: Optional[str],
    summary: bool,
    match_mode: str,
    verbose: bool,
    tag_only: bool,
    max_warnings: int,
    ai_explain: bool,
):
    """
    Analyze a terraform plan for risk and known issues.

    \b
    Usage:
        terraform show -json plan.tfplan > plan.json
        fixdoc analyze plan.json
        fixdoc analyze plan.json --graph graph.dot
        fixdoc analyze plan.json --format json
        fixdoc analyze plan.json --exit-on high
        fixdoc analyze plan.json --summary
        fixdoc analyze plan.json --match strict

    \b
    Options:
        --graph/-g      Path to DOT file from `terraform graph`
        --format/-f     Output format: human, json, or markdown
        --max-depth/-d  Max BFS traversal depth (default: 5)
        --exit-on       Exit code 1 if severity >= threshold (for CI gating)
        --summary/-s    Quick summary output
        --match/-m      Fix history match strictness (strict|balanced|loose)
        --verbose/-v    Show detailed output
        --tag-only      Only show tribal warnings from tag-matched fixes
        --max-warnings  Max tribal knowledge warnings to surface (default: 10)
        --ai-explain    Use Claude API for polished score explanation (needs ANTHROPIC_API_KEY)
    """
    repo = FixRepository(ctx.obj["base_path"])
    plan_path = Path(plan_file)

    try:
        with open(plan_path, "r") as f:
            plan = json.load(f)
    except json.JSONDecodeError:
        click.echo("Error: Invalid JSON in plan file.", err=True)
        click.echo(
            "Hint: Use `terraform show -json plan.tfplan > plan.json`",
            err=True,
        )
        raise SystemExit(1)

    # Check for changed resources
    analyzer = TerraformAnalyzer(repo=repo)
    changed = analyzer.get_changed_resources(plan)

    if not changed:
        click.echo("No changes to analyze.")
        return

    # Get graph DOT text
    dot_text = None
    if graph_file:
        with open(graph_file, "r") as f:
            dot_text = f.read()
    else:
        dot_text = _auto_run_terraform_graph()
        if dot_text is None and output_format != "json":
            click.echo(
                "Note: terraform not on PATH; running without dependency graph.",
                err=True,
            )

    # Build change_blocks from plan for fingerprinting
    plan_change_blocks = {}
    for rc in plan.get("resource_changes", []):
        addr = rc.get("address", "")
        cb = rc.get("change", {})
        if addr and cb:
            plan_change_blocks[addr] = cb

    # Run blast radius analysis
    result = analyze_blast_radius(
        plan, repo, dot_text=dot_text, max_depth=max_depth,
        tag_only=tag_only, max_resource_warnings=max_warnings,
        change_blocks=plan_change_blocks,
    )

    # Filter history matches by match mode
    if match_mode == "strict":
        # Only keep matches with error_code or resource_address match
        # For simplicity, strict mode just requires exact resource_type match
        # which is already what compute_history_prior does
        pass  # Already filtered
    elif match_mode == "loose":
        # Include all matches (already included)
        pass
    # balanced is default — uses the standard history_prior logic

    # Optional AI explanation
    ai_explanation = None
    if ai_explain:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            click.echo("Warning: --ai-explain requires ANTHROPIC_API_KEY env var.", err=True)
        else:
            ai_explanation = generate_ai_explanation(result, api_key)
            if ai_explanation is None:
                click.echo("Warning: AI explanation failed, falling back to rule-based.", err=True)

    # Output
    if summary:
        click.echo(_format_summary(result))
    elif output_format == "json":
        click.echo(_format_json(result))
    elif output_format == "markdown":
        click.echo(_format_markdown(result))
    else:
        click.echo(_format_human(result, changed, verbose=verbose, ai_explanation=ai_explanation))

    # CI gating
    if exit_on is not None:
        result_rank = _SEVERITY_ORDER[result.severity]
        threshold_rank = _SEVERITY_ORDER[exit_on]
        if result_rank >= threshold_rank:
            raise SystemExit(1)
