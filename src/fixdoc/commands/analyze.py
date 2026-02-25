"""Analyze command for fixdoc CLI.

Merges plan analysis + blast radius into a single command.
"""

import json
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

            resources.append(PlanResource(
                address=address,
                resource_type=resource_type,
                name=name,
                cloud_provider=self.detect_cloud_provider(resource_type, provider_name),
                action=action,
                module_path=module_path,
                values=values,
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


def _format_human(result: BlastResult, changed: list[PlanResource], verbose: bool = False) -> str:
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

    # History matches
    if result.history_matches:
        lines.append(f"Risk Warnings from History ({len(result.history_matches)}):")
        for hm in result.history_matches:
            fix_id = hm["id"]
            issue = hm["issue"]
            issue_preview = issue[:60] + "..." if len(issue) > 60 else issue
            lines.append(f"  FIX-{fix_id}: {issue_preview}")
        lines.append("")

    # Prior issues for changed resource types (tribal knowledge)
    if result.resource_warnings:
        lines.append(f"Prior Issues for Changed Resources ({len(result.resource_warnings)}):")
        for w in result.resource_warnings:
            short_id = w["short_id"]
            issue = w["issue"] or ""
            resolution = w["resolution"] or ""
            matched = w.get("matched_resources", [])
            created_at = (w.get("created_at") or "")[:10]

            if verbose:
                score = w.get("score", 0)
                reason = w.get("match_reason", "")
                lines.append(f"  [score:{score} | {reason}] FIX-{short_id}: {issue}")
                lines.append(f"   Resolution: {resolution}")
            else:
                issue_disp = issue[:80] + "..." if len(issue) > 80 else issue
                res_disp = resolution[:80] + "..." if len(resolution) > 80 else resolution
                lines.append(f"  FIX-{short_id}: {issue_disp}")
                lines.append(f"   Resolution: {res_disp}")

            if len(matched) > 1:
                display = matched[:10]
                applies = ", ".join(f"{r['address']} ({r['action']})" for r in display)
                extra = len(matched) - 10
                if extra > 0:
                    lines.append(f"   Applies to: {applies}")
                    lines.append(f"               [+{extra} more]")
                else:
                    lines.append(f"   Applies to: {applies}")

            lines.append(f"   Captured: {created_at}")

            if verbose and w.get("tags"):
                lines.append(f"   Tags: {w['tags']}")

            lines.append("")

        lines.append("Run `fixdoc show <short_id>` for full details.")
        lines.append("")

    # Recommended checks
    if result.checks:
        lines.append("Recommended Checks:")
        for check in result.checks:
            lines.append(f"  - {check}")
        lines.append("")

    return "\n".join(lines)


def _format_json(result: BlastResult) -> str:
    """Format blast radius result as JSON."""
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
    type=click.Choice(["human", "json"]),
    default="human",
    help="Output format.",
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
        --format/-f     Output format: human or json
        --max-depth/-d  Max BFS traversal depth (default: 5)
        --exit-on       Exit code 1 if severity >= threshold (for CI gating)
        --summary/-s    Quick summary output
        --match/-m      Fix history match strictness (strict|balanced|loose)
        --verbose/-v    Show detailed output
        --tag-only      Only show tribal warnings from tag-matched fixes
        --max-warnings  Max tribal knowledge warnings to surface (default: 10)
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

    # Run blast radius analysis
    result = analyze_blast_radius(
        plan, repo, dot_text=dot_text, max_depth=max_depth,
        tag_only=tag_only, max_resource_warnings=max_warnings,
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

    # Output
    if summary:
        click.echo(_format_summary(result))
    elif output_format == "json":
        click.echo(_format_json(result))
    else:
        click.echo(_format_human(result, changed, verbose=verbose))

    # CI gating
    if exit_on is not None:
        result_rank = _SEVERITY_ORDER[result.severity]
        threshold_rank = _SEVERITY_ORDER[exit_on]
        if result_rank >= threshold_rank:
            raise SystemExit(1)
