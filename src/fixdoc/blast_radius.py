"""Blast radius analysis engine for fixdoc.

Estimates which identities, workloads, and resources are most likely
affected by infrastructure changes before they're applied. Combines
Terraform plan JSON, the terraform graph dependency DAG, and FixDoc's
fix history into a weighted BlastScore (0-100) with explainable
propagation paths.
"""

import json
import re
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .models import Fix
from .storage import FixRepository

# ---------------------------------------------------------------------------
# Control-point classification
# ---------------------------------------------------------------------------

# Maps resource type prefix -> (category, criticality 0-1)
CONTROL_POINT_PATTERNS: dict[str, tuple[str, float]] = {
    # AWS IAM
    "aws_iam_role_policy_attachment": ("iam", 0.9),
    "aws_iam_role_policy": ("iam", 0.9),
    "aws_iam_policy_attachment": ("iam", 0.9),
    "aws_iam_group_policy_attachment": ("iam", 0.85),
    "aws_iam_user_policy_attachment": ("iam", 0.85),
    "aws_iam_role": ("iam", 0.9),
    "aws_iam_policy": ("iam", 0.85),
    "aws_iam_user": ("iam", 0.8),
    "aws_iam_group": ("iam", 0.75),
    # Azure RBAC
    "azurerm_role_assignment": ("rbac", 0.9),
    "azurerm_key_vault_access_policy": ("rbac", 0.85),
    "azurerm_role_definition": ("rbac", 0.85),
    # GCP IAM
    "google_project_iam": ("iam", 0.9),
    "google_service_account": ("iam", 0.85),
    # Network boundaries
    "aws_security_group": ("network", 0.8),
    "aws_network_acl": ("network", 0.8),
    "aws_route_table": ("network", 0.7),
    "azurerm_network_security_group": ("network", 0.8),
    "azurerm_firewall_rule": ("network", 0.85),
    "google_compute_firewall": ("network", 0.8),
}

# Sensitive value patterns for redaction
SENSITIVE_PATTERNS = re.compile(
    r"(password|secret|token|api_key|private_key|access_key|credentials)",
    re.IGNORECASE,
)

# Recommended checks per control-point category
CATEGORY_CHECKS: dict[str, list[str]] = {
    "iam": [
        "Review IAM policy least-privilege before applying",
        "Check service account permissions",
    ],
    "rbac": [
        "Review RBAC role assignment scope",
        "Verify key vault access policy changes",
    ],
    "network": [
        "Verify security group rules",
        "Check for open 0.0.0.0/0 rules",
    ],
}

DELETE_CHECKS = ["Confirm resource is not referenced by other stacks"]

# Category tags that indicate a fix is relevant to a specific concern domain.
# Resource-type-only tags (e.g. "aws_instance") are NOT sufficient — a fix must
# have at least one of these to surface in Phase 2 history matching.
_HISTORY_CATEGORY_TAGS: frozenset = frozenset({
    "networking", "network", "rbac", "iam", "dns", "quota", "state",
    "state-lock", "auth", "authentication", "authorization", "acl",
    "route", "routing", "connectivity", "ingress", "egress", "security",
    "firewall", "k8s", "kubernetes", "key_vault", "vault", "certificate",
    "cert", "database", "db", "storage", "permissions",
})

# Action points for the linear scoring formula
ACTION_POINTS = {
    "delete": 20,
    "replace": 25,
    "update": 5,
    "create": 8,
}

_ACTIONABLE_ACTIONS = frozenset({"create", "update", "delete", "replace"})

# Discount multipliers for all-create (greenfield) plans.
# Creating new resources poses lower risk than modifying existing ones.
GREENFIELD_MULTIPLIER = 0.3           # non-boundary creates
GREENFIELD_BOUNDARY_MULTIPLIER = 0.5  # boundary creates (smaller discount — still risky if misconfigured)
GREENFIELD_IMPACT_MULTIPLIER = 0.25   # fraction of normal L1/L2 weight for cross-boundary edges


# Sensitive IAM policy fields — a change to any of these triggers Layer 1 scoring.
_SENSITIVE_IAM_FIELDS: frozenset = frozenset({
    "assume_role_policy", "policy", "inline_policy",
    "managed_policy_arns", "policy_arn",
})

# Matches cross-account ARNs like arn:aws:iam::123456789012:root
_CROSS_ACCOUNT_RE = re.compile(r"^arn:aws:iam::\d+:root$")


# ---------------------------------------------------------------------------
# Attribute categories for change fingerprinting
# ---------------------------------------------------------------------------

ATTR_CATEGORIES = {
    "ingress": "networking", "egress": "networking", "cidr_blocks": "networking",
    "security_groups": "networking", "from_port": "networking", "to_port": "networking",
    "subnet_id": "networking", "vpc_id": "networking", "route_table_id": "networking",
    "instance_type": "sizing", "instance_class": "sizing", "node_type": "sizing",
    "policy": "iam", "assume_role_policy": "iam", "role": "iam", "policy_arn": "iam",
    "managed_policy_arns": "iam", "inline_policy": "iam",
    "tags": "metadata", "name": "naming", "description": "metadata",
    "ami": "image", "image_id": "image",
    "engine_version": "versioning", "runtime": "versioning",
    "cidr_block": "networking", "availability_zone": "placement",
    "kms_key_id": "encryption", "encrypted": "encryption",
    "acl": "access", "versioning": "storage", "bucket": "naming",
}

# Attribute-aware recommended checks
ATTR_CHECKS = {
    ("aws_security_group", "ingress"): "Review new ingress rules for overly permissive CIDRs (0.0.0.0/0)",
    ("aws_security_group", "egress"): "Review egress rules for least-privilege outbound access",
    ("aws_iam_role", "assume_role_policy"): "Audit trust policy principals for wildcard or cross-account access",
    ("aws_iam_role_policy", "policy"): "Review inline policy for least-privilege",
    ("aws_iam_policy", "policy"): "Review managed policy document for overly broad permissions",
    ("aws_instance", "instance_type"): "Verify instance type availability in target AZ and region",
    ("aws_instance", "ami"): "Confirm AMI exists and is shared to this account",
    ("aws_db_instance", "engine_version"): "Check RDS engine version compatibility and upgrade path",
    ("aws_s3_bucket", "acl"): "Review bucket ACL — prefer bucket policies over ACLs",
    ("aws_lambda_function", "runtime"): "Verify runtime is not deprecated",
    ("aws_vpc", "cidr_block"): "CIDR change forces replacement — verify peering/route table dependencies",
    ("aws_subnet", "cidr_block"): "Subnet CIDR change forces replacement — check ENI dependencies",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class BlastNode:
    """A resource node in the blast radius graph."""

    address: str
    resource_type: str
    action: str  # create, update, delete, replace, no-op
    cloud_provider: str = "unknown"
    is_control_point: bool = False
    criticality: float = 0.0
    category: str = ""
    sensitivity_delta: float = 0.0
    sensitivity_reason: str = ""
    wildcard_trust: bool = False
    change_fingerprint: dict = field(default_factory=dict)


@dataclass
class AffectedResource:
    """A resource reached by BFS propagation from a control point."""

    address: str
    resource_type: str
    depth: int
    path: list[str] = field(default_factory=list)


@dataclass
class BlastResult:
    """Complete result of a blast radius analysis."""

    analysis_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    score: float = 0.0
    severity: str = "low"
    changes: list[dict] = field(default_factory=list)
    control_points: list[dict] = field(default_factory=list)
    affected: list[dict] = field(default_factory=list)
    why_paths: list[dict] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    history_matches: list[dict] = field(default_factory=list)
    plan_summary: dict = field(default_factory=dict)
    resource_warnings: list[dict] = field(default_factory=list)
    score_explanation: list[dict] = field(default_factory=list)
    relevant_fixes: list[dict] = field(default_factory=list)
    contextual_checks: list[dict] = field(default_factory=list)


@dataclass
class ScoreExplanation:
    """A single bullet-point explanation of a blast score contribution."""

    label: str    # Human-readable description
    delta: float  # Score contribution (positive = adds to score)
    kind: str     # "action", "iam", "impact", "history", "modifier"


def is_actionable_change(node: BlastNode) -> bool:
    """Return True if this node represents a real infrastructure change."""
    return node.action in _ACTIONABLE_ACTIONS


# ---------------------------------------------------------------------------
# Control-point classification
# ---------------------------------------------------------------------------


def classify_control_point(resource_type: str) -> Optional[tuple[str, float]]:
    """Classify a resource type as a control point.

    Uses prefix matching so e.g. 'google_project_iam_member' matches
    'google_project_iam'.

    Returns (category, criticality) or None if not a control point.
    """
    rt_lower = resource_type.lower()
    # Try longest prefix first so more specific patterns win
    best_match = None
    best_len = 0
    for prefix, (category, criticality) in CONTROL_POINT_PATTERNS.items():
        if rt_lower.startswith(prefix) and len(prefix) > best_len:
            best_match = (category, criticality)
            best_len = len(prefix)
    return best_match


def is_boundary_resource(resource_type: str) -> bool:
    """Check if a resource type is a boundary/control-point resource."""
    return classify_control_point(resource_type) is not None


# ---------------------------------------------------------------------------
# DOT graph parser
# ---------------------------------------------------------------------------

# Matches edges: "node_a" -> "node_b" or unquoted node_a -> node_b
_EDGE_RE = re.compile(
    r'"([^"]+)"\s*->\s*"([^"]+)"'
    r"|"
    r"(\S+)\s*->\s*(\S+)"
)


def _normalize_tf_node(name: str) -> str:
    """Normalize a Terraform graph node name to match plan addresses.

    Strips '[root] ' prefix and '(expand)'/'(close)' suffixes.
    """
    name = name.strip()
    if name.startswith("[root] "):
        name = name[7:]
    name = re.sub(r"\s*\(expand\)\s*$", "", name)
    name = re.sub(r"\s*\(close\)\s*$", "", name)
    return name.strip()


def parse_dot_graph(dot_text: str) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Parse a Terraform DOT graph into forward and reverse adjacency lists.

    Returns (forward_adj, reverse_adj) where forward means A -> B
    (A depends on B or B is downstream of A, depending on TF graph direction).
    """
    forward: dict[str, set[str]] = {}
    reverse: dict[str, set[str]] = {}

    for line in dot_text.splitlines():
        stripped = line.strip()
        # Skip comments, subgraph declarations, closing braces
        if (
            stripped.startswith("//")
            or stripped.startswith("#")
            or stripped.startswith("subgraph")
            or stripped.startswith("digraph")
            or stripped in ("}", "{")
        ):
            continue

        m = _EDGE_RE.search(stripped)
        if m:
            if m.group(1) is not None:
                src = _normalize_tf_node(m.group(1))
                dst = _normalize_tf_node(m.group(2))
            else:
                src = _normalize_tf_node(m.group(3))
                dst = _normalize_tf_node(m.group(4))

            forward.setdefault(src, set()).add(dst)
            forward.setdefault(dst, set())
            reverse.setdefault(dst, set()).add(src)
            reverse.setdefault(src, set())

    return forward, reverse


def _addr_in_plan(addr: str, changed_addresses: set) -> bool:
    """Return True if addr is in the plan, including as the base name of indexed resources.

    terraform graph collapses count/for_each instances like aws_sg.bulk[0..49]
    into a single node aws_sg.bulk — this must be recognised as "in the plan".
    """
    if addr in changed_addresses:
        return True
    prefix = addr + "["
    return any(a.startswith(prefix) for a in changed_addresses)


# ---------------------------------------------------------------------------
# Bounded BFS
# ---------------------------------------------------------------------------


def compute_affected_set(
    start_nodes: list[str],
    adjacency: dict[str, set[str]],
    max_depth: int = 5,
) -> list[AffectedResource]:
    """BFS from start_nodes through adjacency, bounded by max_depth.

    Returns list of AffectedResource with traversal paths.
    Keeps shortest path when reached from multiple starts.
    """
    visited: dict[str, AffectedResource] = {}
    queue: deque[tuple[str, int, list[str]]] = deque()

    for node in start_nodes:
        queue.append((node, 0, [node]))
        visited[node] = AffectedResource(
            address=node, resource_type="", depth=0, path=[node]
        )

    while queue:
        current, depth, path = queue.popleft()
        if depth >= max_depth:
            continue

        for neighbor in adjacency.get(current, set()):
            if neighbor not in visited:
                new_path = path + [neighbor]
                visited[neighbor] = AffectedResource(
                    address=neighbor,
                    resource_type="",
                    depth=depth + 1,
                    path=new_path,
                )
                queue.append((neighbor, depth + 1, new_path))

    # Remove start nodes from results (they are the changes, not affected)
    return [ar for ar in visited.values() if ar.address not in start_nodes]


def compute_tiered_affected(
    changed_nodes: list[BlastNode],
    adjacency: dict[str, set[str]],
    max_depth: int = 5,
) -> tuple[list[AffectedResource], list[AffectedResource]]:
    """Compute tiered affected sets: L1 (direct) and L2 (indirect).

    L2 is only populated if any L0 node is a boundary resource or
    involves a delete/replace action.

    Returns (l1_affected, l2_affected).
    """
    start_addrs = [n.address for n in changed_nodes]

    # Always compute L1 (depth 1)
    all_affected = compute_affected_set(start_addrs, adjacency, max_depth=max_depth)
    l1 = [ar for ar in all_affected if ar.depth == 1]
    l2 = [ar for ar in all_affected if ar.depth >= 2]

    # Gate L2: only include if L0 has boundary resources or delete/replace
    has_boundary = any(is_boundary_resource(n.resource_type) for n in changed_nodes)
    has_destructive = any(n.action in ("delete", "replace") for n in changed_nodes)

    if not (has_boundary or has_destructive):
        l2 = []

    return l1, l2


# ---------------------------------------------------------------------------
# IAM sensitivity helpers
# ---------------------------------------------------------------------------


def _extract_principals(policy_json) -> set:
    """Extract all principal strings from an IAM policy JSON.

    Handles both JSON string and dict inputs.
    Supports: Principal: "*", {"Service": "..."}, {"Service": [...]},
              {"AWS": "..."}, etc.
    Returns set[str].
    """
    if isinstance(policy_json, str):
        try:
            policy = json.loads(policy_json)
        except (ValueError, TypeError):
            return set()
    elif isinstance(policy_json, dict):
        policy = policy_json
    else:
        return set()

    principals: set = set()
    for stmt in policy.get("Statement", []):
        p = stmt.get("Principal")
        if p is None:
            continue
        if isinstance(p, str):
            principals.add(p)
        elif isinstance(p, dict):
            for v in p.values():
                if isinstance(v, str):
                    principals.add(v)
                elif isinstance(v, list):
                    principals.update(str(x) for x in v)
    return principals


def _compute_iam_sensitivity(change_block: dict) -> tuple:
    """Compute IAM policy sensitivity delta from a plan change block.

    Returns (delta: float, reason: str, wildcard_trust: bool).

    Layer 1 — sensitive field gate (+8 if any key IAM policy field changed).
    Layer 2 — principal expansion, only when assume_role_policy changed:
        new *.amazonaws.com service:          +10
        new arn:aws:iam::<id>:root (cross-acct): +20
        new "*" wildcard:                     sets wildcard_trust=True
    Principal delta is capped at 25.
    Total delta = layer1 + min(principal_delta, 25).
    """
    before = change_block.get("before") or {}
    after = change_block.get("after") or {}

    layer1_delta = 0.0
    assume_role_changed = False

    for field_name in _SENSITIVE_IAM_FIELDS:
        if before.get(field_name) != after.get(field_name):
            layer1_delta = 8.0
            if field_name == "assume_role_policy":
                assume_role_changed = True

    principal_delta = 0.0
    reason_parts: list = []
    wildcard_trust = False

    if assume_role_changed:
        before_principals = _extract_principals(before.get("assume_role_policy", "{}"))
        after_principals = _extract_principals(after.get("assume_role_policy", "{}"))
        added = after_principals - before_principals

        for p in added:
            if p == "*":
                wildcard_trust = True
                reason_parts.append("wildcard principal")
            elif _CROSS_ACCOUNT_RE.match(p):
                principal_delta += 20.0
                reason_parts.append(p)
            elif p.endswith(".amazonaws.com"):
                principal_delta += 10.0
                reason_parts.append(p)

        principal_delta = min(principal_delta, 25.0)

    delta = layer1_delta + principal_delta
    reason = ", ".join(reason_parts) if reason_parts else (
        "policy field changed" if layer1_delta > 0 else ""
    )
    return delta, reason, wildcard_trust


# ---------------------------------------------------------------------------
# Blast score formula — linear
# ---------------------------------------------------------------------------


def _normalize_action(actions: list[str]) -> str:
    """Normalize a Terraform actions list to a single action string.

    Treats ["create", "delete"] as "replace".
    """
    action_set = set(actions)
    if "create" in action_set and "delete" in action_set:
        return "replace"
    if "delete" in action_set:
        return "delete"
    if "update" in action_set:
        return "update"
    if "create" in action_set:
        return "create"
    return "no-op"


def compute_blast_score(
    changed_nodes: list[BlastNode],
    l1_count: int,
    l2_count: int,
    history_match_count: int,
) -> float:
    """Compute blast score 0-100 using linear formula.

    Score components:
    1. Action points for each changed (L0) resource
    2. Impact points from dependents (L1 + L2)
    3. History overlay

    For greenfield plans (all creates):
    - Non-boundary creates: GREENFIELD_MULTIPLIER (0.3x)
    - Boundary creates: GREENFIELD_BOUNDARY_MULTIPLIER (0.5x) — smaller discount
    - L1/L2: caller pre-filters to cross-boundary existing-infra edges only,
      weighted at 1.5 * GREENFIELD_IMPACT_MULTIPLIER (0.375x normal)
    """
    score = 0.0

    # No-ops/reads are already excluded by analyze_blast_radius() before this call.
    # Greenfield: all active changes are creates.
    is_greenfield = bool(changed_nodes) and all(
        node.action == "create" for node in changed_nodes
    )

    # 1. Action points for each L0 resource
    all_updates_no_boundary = True
    action_points = 0.0

    for node in changed_nodes:
        points = ACTION_POINTS.get(node.action, 0)
        if is_boundary_resource(node.resource_type):
            points *= 1.5
            if is_greenfield:
                points *= GREENFIELD_BOUNDARY_MULTIPLIER
        else:
            if is_greenfield:
                points *= GREENFIELD_MULTIPLIER
        if node.action != "update" or is_boundary_resource(node.resource_type):
            all_updates_no_boundary = False
        points += node.sensitivity_delta  # IAM policy change bonus (not discounted)
        action_points += points

    score += action_points

    # 2. Impact points from dependents
    # For greenfield: l1_count/l2_count are pre-filtered by analyze_blast_radius()
    # to only count resources NOT in the plan (cross-boundary existing-infra edges).
    impacted_count = min(l1_count + l2_count, 25)
    if is_greenfield:
        # New resources touching existing infra: low weight (0.25 × normal 1.5)
        impact_multiplier = 1.5 * GREENFIELD_IMPACT_MULTIPLIER
    elif all_updates_no_boundary:
        impact_multiplier = 0.5
    else:
        impact_multiplier = 1.5
    score += impacted_count * impact_multiplier

    # 3. History overlay
    score += min(history_match_count * 5, 15)

    # Greenfield ceiling: all-creates with no cross-boundary existing-infra impact
    # cannot exceed MEDIUM. Volume of new resources ≠ blast radius on live infra.
    if is_greenfield and l1_count == 0 and l2_count == 0:
        score = min(score, 45.0)

    # Wildcard trust floor: a "*" principal in an IAM trust policy is always HIGH.
    if any(n.wildcard_trust for n in changed_nodes):
        score = max(score, 50.0)

    # Clamp
    score = min(100.0, max(0.0, score))
    return round(score, 1)


def severity_label(score: float) -> str:
    """Map blast score to severity label."""
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


def build_score_explanation(
    nodes: list[BlastNode],
    l1_count: int,
    l2_count: int,
    history_count: int,
) -> list[ScoreExplanation]:
    """Build rule-based score explanation bullets mirroring compute_blast_score().

    Returns a list of ScoreExplanation objects describing each score contribution.
    """
    if not nodes:
        return []

    explanations: list[ScoreExplanation] = []

    is_greenfield = all(node.action == "create" for node in nodes)
    all_updates_no_boundary = all(
        node.action == "update" and not is_boundary_resource(node.resource_type)
        for node in nodes
    )

    # Action bullets — one per changed node
    for node in nodes:
        base_points = ACTION_POINTS.get(node.action, 0)
        is_boundary = is_boundary_resource(node.resource_type)
        points = float(base_points)
        qualifiers: list[str] = []

        if is_boundary:
            points *= 1.5
            qualifiers.append("boundary resource")
            if is_greenfield:
                points *= GREENFIELD_BOUNDARY_MULTIPLIER
                qualifiers.append("greenfield")
        else:
            if is_greenfield:
                points *= GREENFIELD_MULTIPLIER
                qualifiers.append("greenfield")

        if points > 0:
            qualifier_str = f" ({', '.join(qualifiers)})" if qualifiers else ""
            label = f"{node.action} {node.address}{qualifier_str}"
            explanations.append(
                ScoreExplanation(label=label, delta=round(points, 1), kind="action")
            )

        # IAM sensitivity bullet
        if node.sensitivity_delta > 0:
            if node.sensitivity_reason and node.sensitivity_reason != "policy field changed":
                label = f"IAM trust expanded on {node.address}: {node.sensitivity_reason}"
            else:
                label = f"IAM policy field changed on {node.address}"
            explanations.append(
                ScoreExplanation(label=label, delta=node.sensitivity_delta, kind="iam")
            )

        # Wildcard trust modifier
        if node.wildcard_trust:
            label = "Wildcard principal (*) in IAM trust policy — forces minimum HIGH score"
            explanations.append(
                ScoreExplanation(label=label, delta=0.0, kind="modifier")
            )

    # Impact bullet
    impacted = min(l1_count + l2_count, 25)
    if impacted > 0:
        if is_greenfield:
            impact_multiplier = 1.5 * GREENFIELD_IMPACT_MULTIPLIER
        elif all_updates_no_boundary:
            impact_multiplier = 0.5
        else:
            impact_multiplier = 1.5
        delta = round(impacted * impact_multiplier, 1)
        label = f"{impacted} downstream resource(s) affected via dependency graph"
        explanations.append(
            ScoreExplanation(label=label, delta=delta, kind="impact")
        )

    # History bullet
    if history_count > 0:
        delta = float(min(history_count * 5, 15))
        plural = "s" if history_count > 1 else ""
        match_plural = "es" if history_count > 1 else ""
        label = (
            f"Prior similar incident{plural} detected in fix history "
            f"({history_count} match{match_plural})"
        )
        explanations.append(
            ScoreExplanation(label=label, delta=delta, kind="history")
        )

    # Greenfield cap modifier
    if is_greenfield and l1_count == 0 and l2_count == 0:
        label = "Greenfield plan (all creates, no cross-boundary impact) — capped at MEDIUM"
        explanations.append(
            ScoreExplanation(label=label, delta=0.0, kind="modifier")
        )

    return explanations


# ---------------------------------------------------------------------------
# History-prior scoring
# ---------------------------------------------------------------------------


def _history_cluster_key(fix: Fix) -> str:
    """Return dedup cluster key: first CamelCase error token, or first 4 words."""
    issue = fix.issue or ""
    m = re.search(r'[A-Z][a-z]+(?:[A-Z][a-z]+)+', issue)
    if m:
        return m.group()
    words = re.sub(r'[^\w\s]', '', issue).lower().split()[:4]
    return ' '.join(words)


def _dedup_history_candidates(
    candidates: list[tuple[Fix, str]]
) -> list[tuple[Fix, str]]:
    """Cluster by error fingerprint; keep most-complete fix per cluster.

    Most complete = has error_excerpt, then most recent created_at.
    """
    clusters: dict[str, tuple[Fix, str]] = {}
    for fix, rt in candidates:
        key = _history_cluster_key(fix)
        if key not in clusters:
            clusters[key] = (fix, rt)
        else:
            existing_fix, _ = clusters[key]
            existing_score = (1 if existing_fix.error_excerpt else 0, existing_fix.created_at)
            new_score = (1 if fix.error_excerpt else 0, fix.created_at)
            if new_score > existing_score:
                clusters[key] = (fix, rt)
    return list(clusters.values())


def compute_history_prior(
    changed_resource_types: list[str],
    changed_nodes: list[BlastNode],
    repo: FixRepository,
) -> tuple[int, list[dict]]:
    """Compute history match count from fix database.

    Returns (match_count, list_of_matching_fix_dicts).
    Only returns matches when:
    - A changed resource is a control point (boundary), OR
    - Any action is delete/replace, OR
    - A fix's issue/error_excerpt mentions a changed resource address exactly.
    Matches are category-tag filtered, deduped, and capped at 3.
    """
    has_boundary = any(is_boundary_resource(n.resource_type) for n in changed_nodes)
    has_destructive = any(n.action in ("delete", "replace") for n in changed_nodes)
    changed_addresses = {n.address.lower() for n in changed_nodes}

    seen_ids: set[str] = set()
    candidates: list[tuple[Fix, str]] = []  # (fix, resource_type)

    # Phase 1: Address-match override — works even without gate
    for fix in repo.list_all():
        fix_searchable = " ".join(filter(None, [fix.issue, fix.error_excerpt])).lower()
        if any(addr in fix_searchable for addr in changed_addresses):
            if fix.id not in seen_ids:
                seen_ids.add(fix.id)
                candidates.append((fix, ""))

    # Phase 2: Resource-type + category tag — only when gate passes
    if has_boundary or has_destructive:
        for rt in changed_resource_types:
            for fix in repo.find_by_resource_type(rt):
                if fix.id in seen_ids:
                    continue
                if not fix.tags:
                    continue
                fix_tags = {t.strip().lower() for t in fix.tags.split(",") if t.strip()}
                if fix_tags & _HISTORY_CATEGORY_TAGS:
                    seen_ids.add(fix.id)
                    candidates.append((fix, rt))

    if not candidates:
        return 0, []

    # Dedup and cap at 3
    deduped = _dedup_history_candidates(candidates)[:3]

    result = [
        {"id": fix.id[:8], "issue": fix.issue, "resource_type": rt}
        for fix, rt in deduped
    ]
    return len(result), result


# ---------------------------------------------------------------------------
# Tribal knowledge: prior fixes for changed resource types
# ---------------------------------------------------------------------------


def find_resource_prior_fixes(
    nodes: list[BlastNode],
    repo: FixRepository,
    max_total: int = 10,
    tag_only: bool = False,
) -> list[dict]:
    """Surface relevant past fixes for all actionable changed resource types.

    Returns a ranked list of warning dicts (capped at max_total), each with:
      id, short_id, issue, resolution, tags, created_at, match_reason,
      score, matched_resources.

    Scoring tiers:
      100 - tag_match: fix is tagged with the resource type
       60 - text_match: resource type appears as a whole word in fix text
    """
    actionable = [n for n in nodes if is_actionable_change(n)]
    if not actionable:
        return []

    # Collect unique resource types
    unique_rts: list[str] = []
    seen_rts: set[str] = set()
    for node in actionable:
        rt = (node.resource_type or "").strip().lower()
        if rt and rt not in seen_rts:
            seen_rts.add(rt)
            unique_rts.append(rt)

    if not unique_rts:
        return []

    # Build type_cache: rt -> list of (fix, match_reason, score)
    type_cache: dict[str, list[tuple]] = {}

    if tag_only:
        for rt in unique_rts:
            matches = []
            for fix in repo.find_by_resource_type(rt):
                matches.append((fix, "tag_match", 100))
            type_cache[rt] = matches
    else:
        all_fixes = repo.list_all()
        # Pre-compile regex per rt
        rt_patterns = {
            rt: re.compile(r'\b' + re.escape(rt) + r'\b', re.IGNORECASE)
            for rt in unique_rts
        }
        for rt in unique_rts:
            tag_match_ids: set[str] = set()
            matches = []
            for fix in repo.find_by_resource_type(rt):
                tag_match_ids.add(fix.id)
                matches.append((fix, "tag_match", 100))
            pattern = rt_patterns[rt]
            for fix in all_fixes:
                if fix.id in tag_match_ids:
                    continue
                searchable = (fix.issue or "") + " " + (fix.resolution or "") + " " + (fix.error_excerpt or "")
                if pattern.search(searchable):
                    matches.append((fix, "text_match", 60))
            type_cache[rt] = matches

    # Aggregate: one entry per fix id; highest score/reason wins
    # Build map: fix_id -> warning dict
    warnings_by_fix_id: dict[str, dict] = {}

    for node in actionable:
        rt = (node.resource_type or "").strip().lower()
        if not rt:
            continue
        for fix, reason, score in type_cache.get(rt, []):
            if fix.id not in warnings_by_fix_id:
                warnings_by_fix_id[fix.id] = {
                    "id": fix.id,
                    "short_id": fix.id[:8],
                    "issue": fix.issue,
                    "resolution": fix.resolution,
                    "tags": fix.tags or "",
                    "created_at": fix.created_at or "",
                    "match_reason": reason,
                    "score": score,
                    "matched_resources": [{"address": node.address, "action": node.action}],
                }
            else:
                entry = warnings_by_fix_id[fix.id]
                # Upgrade score/reason if this match is higher tier
                if score > entry["score"]:
                    entry["score"] = score
                    entry["match_reason"] = reason
                # Add resource if not already listed
                existing_addrs = {r["address"] for r in entry["matched_resources"]}
                if node.address not in existing_addrs:
                    entry["matched_resources"].append({"address": node.address, "action": node.action})

    if not warnings_by_fix_id:
        return []

    # Sort: (-score, -created_at DESC, id ASC) using stable multi-pass sort
    # Pass 1: id ASC (stable baseline)
    sorted_warnings = sorted(warnings_by_fix_id.values(), key=lambda w: w["id"])
    # Pass 2: created_at DESC (stable, ties broken by id from pass 1)
    sorted_warnings.sort(key=lambda w: (w["created_at"] or ""), reverse=True)
    # Pass 3: score DESC (stable, ties broken by date then id from earlier passes)
    sorted_warnings.sort(key=lambda w: -w["score"])

    # Sort matched_resources by address
    for w in sorted_warnings:
        w["matched_resources"].sort(key=lambda r: r["address"])

    return sorted_warnings[:max_total]


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def redact_plan_values(change_block: dict) -> dict:
    """Redact sensitive values from a plan change block.

    1. Honors sensitive_values markers from the plan JSON.
    2. Pattern-matches keys against SENSITIVE_PATTERNS.
    3. Replaces values with '[REDACTED]'.
    """
    result = {}
    sensitive_keys = set()

    # Collect keys flagged by Terraform's sensitive_values
    for phase in ("before_sensitive", "after_sensitive"):
        sv = change_block.get(phase)
        if isinstance(sv, dict):
            _collect_sensitive_keys(sv, "", sensitive_keys)

    for key, value in change_block.items():
        if key in ("before_sensitive", "after_sensitive"):
            continue
        if isinstance(value, dict):
            result[key] = _redact_dict(value, sensitive_keys, "")
        else:
            result[key] = value

    return result


def _collect_sensitive_keys(
    sensitive_map: dict, prefix: str, out: set[str]
) -> None:
    """Recursively collect keys marked as sensitive."""
    for key, val in sensitive_map.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if val is True:
            out.add(full_key)
        elif isinstance(val, dict):
            _collect_sensitive_keys(val, full_key, out)


def _redact_dict(
    d: dict, sensitive_keys: set[str], prefix: str
) -> dict:
    """Recursively redact sensitive values in a dict."""
    result = {}
    for key, val in d.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if full_key in sensitive_keys or SENSITIVE_PATTERNS.search(key):
            result[key] = "[REDACTED]"
        elif isinstance(val, dict):
            result[key] = _redact_dict(val, sensitive_keys, full_key)
        else:
            result[key] = val
    return result


# ---------------------------------------------------------------------------
# Recommended checks generator
# ---------------------------------------------------------------------------


def generate_checks(
    control_points: list[BlastNode],
    has_deletes: bool,
) -> list[str]:
    """Generate recommended checks based on control point categories."""
    checks: list[str] = []
    seen_categories: set[str] = set()

    for cp in control_points:
        if cp.category and cp.category not in seen_categories:
            seen_categories.add(cp.category)
            checks.extend(CATEGORY_CHECKS.get(cp.category, []))

    if has_deletes:
        checks.extend(DELETE_CHECKS)

    return checks


# ---------------------------------------------------------------------------
# Change fingerprint extraction
# ---------------------------------------------------------------------------


def extract_change_fingerprint(change_block: dict) -> dict:
    """Extract a structured fingerprint from a Terraform plan change block.

    Diffs before vs after at the top level only. Returns changed attribute
    names, count, semantic categories, and whether sensitive fields changed.
    """
    before = change_block.get("before") or {}
    after = change_block.get("after") or {}
    actions = change_block.get("actions", [])
    action = _normalize_action(actions) if actions else "update"

    # For creates: all after keys are "changed"; for deletes: all before keys
    if action == "create":
        changed_attrs = sorted(k for k in after if k != "id")
    elif action == "delete":
        changed_attrs = sorted(k for k in before if k != "id")
    else:
        changed_attrs = sorted(
            k for k in set(list(before.keys()) + list(after.keys()))
            if k != "id" and before.get(k) != after.get(k)
        )

    attr_categories = set()
    for attr in changed_attrs:
        cat = ATTR_CATEGORIES.get(attr)
        if cat:
            attr_categories.add(cat)

    sensitive_changed = any(
        SENSITIVE_PATTERNS.search(attr) for attr in changed_attrs
    )

    return {
        "changed_attrs": changed_attrs,
        "changed_attr_count": len(changed_attrs),
        "attr_categories": attr_categories,
        "action": action,
        "sensitive_changed": sensitive_changed,
    }


# ---------------------------------------------------------------------------
# Unified smart matching
# ---------------------------------------------------------------------------

# Error code extraction patterns (reused from suggestions.py logic)
_ERROR_CODE_RE = re.compile(
    r'(?:api error |error:\s*|code[:\s]*["\']?)(\w+(?:\.\w+)*)',
    re.IGNORECASE,
)
_CAMEL_CASE_RE = re.compile(r'\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b')


def _extract_error_codes_from_text(text: str) -> set:
    """Extract error codes from fix text (issue, error_excerpt, tags)."""
    codes = set()
    for m in _ERROR_CODE_RE.finditer(text):
        codes.add(m.group(1).lower())
    for m in _CAMEL_CASE_RE.finditer(text):
        codes.add(m.group(1).lower())
    return codes


def _extract_module_path(address: str) -> Optional[str]:
    """Extract module path prefix from a resource address.

    e.g. 'module.networking.aws_vpc.main' -> 'module.networking'
    """
    parts = address.split(".")
    module_parts = []
    i = 0
    while i < len(parts):
        if parts[i] == "module" and i + 1 < len(parts):
            module_parts.append(f"module.{parts[i + 1]}")
            i += 2
        else:
            break
    return ".".join(module_parts) if module_parts else None


def _strip_index(address: str) -> str:
    """Strip indexed suffix from address: aws_sg.bulk[0] -> aws_sg.bulk."""
    idx = address.find("[")
    return address[:idx] if idx >= 0 else address


def _leaf_address(address: str) -> str:
    """Strip module prefix to get leaf resource address.

    module.web.aws_instance.app -> aws_instance.app
    """
    parts = address.split(".")
    # Find the last resource_type.name pair
    i = len(parts) - 1
    while i >= 1:
        if not parts[i - 1].startswith("module"):
            return ".".join(parts[i - 1:])
        i -= 1
    return address


def _fix_matches_resource_type(fix: "Fix", resource_type: str) -> bool:
    """Check if a fix is related to a resource type via tags or text."""
    rt_lower = resource_type.lower()
    # Tag match
    if fix.tags:
        for tag in fix.tags.split(","):
            if tag.strip().lower() == rt_lower:
                return True
    # Text match (word boundary)
    pattern = re.compile(r'\b' + re.escape(rt_lower) + r'\b', re.IGNORECASE)
    searchable = " ".join(filter(None, [fix.issue, fix.resolution, fix.error_excerpt]))
    return bool(pattern.search(searchable))


def find_relevant_fixes(
    nodes: list,
    repo: "FixRepository",
    max_total: int = 10,
) -> list:
    """Find fixes relevant to the changed nodes using multi-signal scoring.

    Replaces both find_resource_prior_fixes() and compute_history_prior().
    Each fix is scored against each changed node. Highest score per fix wins.

    Returns list[dict] with fix_id, score, confidence, match_reason,
    matched_resources, plus full fix fields for display.
    """
    actionable = [n for n in nodes if is_actionable_change(n)]
    if not actionable:
        return []

    all_fixes = repo.list_all()
    if not all_fixes:
        return []

    # Pre-compute per-node data
    node_data = []
    for node in actionable:
        fp = node.change_fingerprint or {}
        changed_attrs = set(fp.get("changed_attrs", []))
        attr_cats = fp.get("attr_categories", set())
        module_path = _extract_module_path(node.address)
        leaf_addr = _leaf_address(node.address)
        stripped_addr = _strip_index(node.address)
        node_data.append({
            "node": node,
            "changed_attrs": changed_attrs,
            "attr_cats": attr_cats,
            "module_path": module_path,
            "leaf_addr": leaf_addr,
            "stripped_addr": stripped_addr,
        })

    now = datetime.now(timezone.utc)

    # Score each fix against each node
    fix_scores: dict = {}  # fix.id -> best result dict

    for fix in all_fixes:
        fix_text = " ".join(filter(None, [fix.issue, fix.resolution, fix.error_excerpt])).lower()
        fix_codes = _extract_error_codes_from_text(fix_text)
        fix_tags = set()
        if fix.tags:
            fix_tags = {t.strip().lower() for t in fix.tags.split(",") if t.strip()}

        # Recency bonus
        recency_bonus = 0
        age_days = None
        if fix.created_at:
            try:
                ts = fix.created_at.replace("Z", "+00:00")
                created = datetime.fromisoformat(ts)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_days = (now - created).days
                if age_days < 90:
                    recency_bonus = 30
            except (ValueError, TypeError):
                pass

        best_score = 0
        best_reason = None
        all_matched_resources = []

        for nd in node_data:
            node = nd["node"]
            rt_lower = (node.resource_type or "").strip().lower()
            score = 0
            primary_signal = None
            primary_detail = ""
            node_supporting = []

            has_rt_match = _fix_matches_resource_type(fix, rt_lower) if rt_lower else False

            # Error code match (150) — requires resource type context
            if fix_codes and has_rt_match:
                for code in fix_codes:
                    if len(code) > 3 and code not in ("error", "failed", "true", "false"):
                        if score < 150:
                            score = 150
                            primary_signal = "error_code"
                            primary_detail = code

            # Address match (120)
            if not primary_signal or score < 120:
                stripped = nd["stripped_addr"].lower()
                leaf = nd["leaf_addr"].lower()
                if stripped in fix_text or leaf in fix_text:
                    if score < 120:
                        if primary_signal:
                            node_supporting.append({"signal": primary_signal, "detail": primary_detail})
                        score = 120
                        primary_signal = "address"
                        primary_detail = node.address
                    else:
                        node_supporting.append({"signal": "address", "detail": node.address})

            # Attribute match (100) — requires resource type context
            if has_rt_match and nd["changed_attrs"]:
                for attr in nd["changed_attrs"]:
                    attr_pattern = re.compile(r'\b' + re.escape(attr) + r'\b', re.IGNORECASE)
                    if attr_pattern.search(fix_text):
                        if score < 100:
                            if primary_signal:
                                node_supporting.append({"signal": primary_signal, "detail": primary_detail})
                            score = 100
                            primary_signal = "attribute"
                            primary_detail = attr
                        else:
                            node_supporting.append({"signal": "attribute", "detail": attr})
                        break

            # Attr category match (80) — requires resource type context
            if has_rt_match and nd["attr_cats"]:
                for cat in nd["attr_cats"]:
                    if cat in fix_tags:
                        if score < 80:
                            if primary_signal:
                                node_supporting.append({"signal": primary_signal, "detail": primary_detail})
                            score = 80
                            primary_signal = "category"
                            primary_detail = cat
                        else:
                            node_supporting.append({"signal": "category", "detail": cat})
                        break

            # Resource type + action (60)
            if has_rt_match and rt_lower in fix_tags:
                action_words = {"delete", "replace", "update", "create"}
                fix_has_action = any(w in fix_text for w in action_words if w == node.action)
                if fix_has_action:
                    if score < 60:
                        if primary_signal:
                            node_supporting.append({"signal": primary_signal, "detail": primary_detail})
                        score = 60
                        primary_signal = "type_action"
                        primary_detail = f"{rt_lower} + {node.action}"
                    else:
                        node_supporting.append({"signal": "type_action", "detail": node.action})

            # Resource type tag only (40)
            if rt_lower and rt_lower in fix_tags and score < 40:
                if primary_signal:
                    node_supporting.append({"signal": primary_signal, "detail": primary_detail})
                score = 40
                primary_signal = "resource_type_tag"
                primary_detail = rt_lower

            # Resource type text only (20)
            if rt_lower and score < 20:
                rt_pattern = re.compile(r'\b' + re.escape(rt_lower) + r'\b', re.IGNORECASE)
                if rt_pattern.search(fix_text):
                    if primary_signal:
                        node_supporting.append({"signal": primary_signal, "detail": primary_detail})
                    score = 20
                    primary_signal = "resource_type_text"
                    primary_detail = rt_lower

            if score == 0:
                continue

            # Module path bonus
            module_bonus = 0
            if nd["module_path"] and nd["module_path"].lower() in fix_text:
                module_bonus = 20
                node_supporting.append({"signal": "module_path", "detail": nd["module_path"]})

            # Recency bonus
            if recency_bonus and age_days is not None:
                node_supporting.append({"signal": "recency", "detail": f"{age_days} days ago"})

            total = score + module_bonus + recency_bonus

            # Track this node as a matched resource
            all_matched_resources.append({"address": node.address, "action": node.action})

            if total > best_score:
                best_score = total
                best_reason = {
                    "signal": primary_signal,
                    "detail": primary_detail,
                    "resource_type": rt_lower,
                    "supporting_signals": node_supporting,
                }

        if best_score == 0:
            continue

        # Deduplicate matched_resources by address
        seen_addrs = set()
        deduped_resources = []
        for mr in all_matched_resources:
            if mr["address"] not in seen_addrs:
                seen_addrs.add(mr["address"])
                deduped_resources.append(mr)

        # Confidence bands
        if best_score >= 120:
            confidence = "high"
        elif best_score >= 60:
            confidence = "medium"
        else:
            confidence = "low"

        best_reason["confidence"] = confidence

        entry = {
            "fix_id": fix.id,
            "id": fix.id,
            "short_id": fix.id[:8],
            "issue": fix.issue,
            "resolution": fix.resolution,
            "tags": fix.tags or "",
            "created_at": fix.created_at or "",
            "error_excerpt": fix.error_excerpt or "",
            "score": best_score,
            "confidence": confidence,
            "match_reason": best_reason,
            "matched_resources": deduped_resources,
        }

        if fix.id not in fix_scores or best_score > fix_scores[fix.id]["score"]:
            fix_scores[fix.id] = entry
        elif fix.id in fix_scores:
            # Merge matched_resources
            existing = fix_scores[fix.id]
            existing_addrs = {r["address"] for r in existing["matched_resources"]}
            for mr in deduped_resources:
                if mr["address"] not in existing_addrs:
                    existing["matched_resources"].append(mr)

    if not fix_scores:
        return []

    # Sort by score desc, then created_at desc
    sorted_fixes = sorted(
        fix_scores.values(),
        key=lambda w: (-w["score"], w.get("created_at", "") or ""),
        reverse=False,
    )
    # Re-sort: score desc (already done), then created_at desc for ties
    sorted_fixes.sort(key=lambda w: (-w["score"], -(len(w.get("created_at", "")))))

    for entry in sorted_fixes:
        entry["matched_resources"].sort(key=lambda r: r["address"])

    return sorted_fixes[:max_total]


# ---------------------------------------------------------------------------
# Contextual checks generator
# ---------------------------------------------------------------------------


def generate_contextual_checks(
    nodes: list,
    relevant_fixes: list,
) -> list:
    """Generate context-aware recommended checks.

    1. Attribute-specific checks from ATTR_CHECKS
    2. History-derived checks from high-confidence relevant fixes
    3. Category fallbacks from CATEGORY_CHECKS
    4. Delete checks for any deletes

    Returns list[dict] with check, source, resource.
    """
    checks = []
    seen_texts = set()
    has_attr_check_categories = set()

    # 1. Attribute-specific checks
    for node in nodes:
        if not is_actionable_change(node):
            continue
        fp = node.change_fingerprint or {}
        rt = (node.resource_type or "").strip().lower()
        for attr in fp.get("changed_attrs", []):
            key = (rt, attr)
            check_text = ATTR_CHECKS.get(key)
            if check_text and check_text not in seen_texts:
                seen_texts.add(check_text)
                checks.append({
                    "check": check_text,
                    "source": "attribute",
                    "resource": node.address,
                })
                # Track categories covered by attr checks
                cat = ATTR_CATEGORIES.get(attr)
                if cat:
                    has_attr_check_categories.add(cat)

    # 2. History-derived checks — only from high-confidence fixes
    history_check_count = 0
    for rf in relevant_fixes:
        if history_check_count >= 2:
            break
        confidence = rf.get("confidence", "low")
        if confidence != "high":
            continue
        resolution = rf.get("resolution", "")
        if not resolution or len(resolution) < 20:
            continue
        if resolution.strip().lower() in ("fixed it", "fixed", "resolved"):
            continue
        check_text = f"Prior fix: {resolution[:80]}"
        if check_text not in seen_texts:
            seen_texts.add(check_text)
            resource = ""
            matched = rf.get("matched_resources", [])
            if matched:
                resource = matched[0]["address"]
            checks.append({
                "check": check_text,
                "source": "history",
                "resource": resource,
            })
            history_check_count += 1

    # 3. Category fallbacks — only when no attribute-specific checks for that category
    seen_categories = set()
    for node in nodes:
        if not is_actionable_change(node):
            continue
        cp = classify_control_point(node.resource_type)
        if cp and cp[0] not in seen_categories:
            seen_categories.add(cp[0])
            if cp[0] not in has_attr_check_categories:
                for check_text in CATEGORY_CHECKS.get(cp[0], []):
                    if check_text not in seen_texts:
                        seen_texts.add(check_text)
                        checks.append({
                            "check": check_text,
                            "source": "category",
                            "resource": node.address,
                        })

    # 4. Delete checks
    has_deletes = any(
        n.action in ("delete", "replace") for n in nodes if is_actionable_change(n)
    )
    if has_deletes:
        for check_text in DELETE_CHECKS:
            if check_text not in seen_texts:
                seen_texts.add(check_text)
                checks.append({
                    "check": check_text,
                    "source": "category",
                    "resource": "",
                })

    return checks


# ---------------------------------------------------------------------------
# Main analysis orchestrator
# ---------------------------------------------------------------------------


def analyze_blast_radius(
    plan: dict,
    repo: FixRepository,
    dot_text: Optional[str] = None,
    max_depth: int = 5,
    tag_only: bool = False,
    max_resource_warnings: int = 10,
    change_blocks: Optional[dict] = None,
) -> BlastResult:
    """Run a full blast radius analysis on a Terraform plan.

    Args:
        plan: Parsed Terraform plan JSON.
        repo: FixRepository for history lookup.
        dot_text: Optional DOT graph text from `terraform graph`.
        max_depth: Max BFS traversal depth.
        tag_only: Only surface tribal warnings from tag-matched fixes.
        max_resource_warnings: Max number of tribal knowledge warnings.
        change_blocks: Optional mapping of address -> raw change block for fingerprinting.

    Returns:
        BlastResult with score, severity, affected resources, etc.
    """
    from .commands.analyze import TerraformAnalyzer

    analyzer = TerraformAnalyzer(repo=repo)
    resources = analyzer.extract_resources(plan)

    # Build change_blocks from plan if not provided
    if change_blocks is None:
        change_blocks = {}
        for rc in plan.get("resource_changes", []):
            addr = rc.get("address", "")
            cb = rc.get("change", {})
            if addr and cb:
                change_blocks[addr] = cb

    # Build nodes and identify control points — changed only
    nodes: list[BlastNode] = []
    control_points: list[BlastNode] = []
    changes: list[dict] = []

    for res in resources:
        action = res.action
        if action in ("no-op", "read", "refresh-only", "unknown"):
            continue

        # Detect replace: ["create", "delete"]
        raw_actions = []
        for rc in plan.get("resource_changes", []):
            if rc.get("address") == res.address:
                raw_actions = rc.get("change", {}).get("actions", [])
                break
        if "create" in raw_actions and "delete" in raw_actions:
            action = "replace"

        cp_info = classify_control_point(res.resource_type)
        is_cp = cp_info is not None
        category = cp_info[0] if cp_info else ""
        criticality = cp_info[1] if cp_info else 0.0

        # Extract change fingerprint
        fingerprint = {}
        cb = change_blocks.get(res.address)
        if cb:
            fingerprint = extract_change_fingerprint(cb)

        node = BlastNode(
            address=res.address,
            resource_type=res.resource_type,
            action=action,
            cloud_provider=res.cloud_provider.value,
            is_control_point=is_cp,
            criticality=criticality,
            category=category,
            change_fingerprint=fingerprint,
        )
        nodes.append(node)

        if is_cp:
            control_points.append(node)
            if node.action == "update" and node.category in ("iam", "rbac"):
                iam_cb = _find_change_block(plan, node.address)
                if iam_cb:
                    delta, reason, wildcard = _compute_iam_sensitivity(iam_cb)
                    node.sensitivity_delta = delta
                    node.sensitivity_reason = reason
                    node.wildcard_trust = wildcard

        changes.append(
            {
                "address": res.address,
                "resource_type": res.resource_type,
                "action": action,
                "cloud_provider": res.cloud_provider.value,
                "is_control_point": is_cp,
                "category": category,
                "criticality": criticality,
            }
        )

    # BFS propagation if graph is available
    l1_affected: list[AffectedResource] = []
    l2_affected: list[AffectedResource] = []
    all_affected: list[AffectedResource] = []

    if dot_text and nodes:
        forward, reverse = parse_dot_graph(dot_text)
        changed_addrs = {n.address for n in nodes}
        extra_seeds: set[str] = set()
        for node in nodes:
            if node.is_control_point:
                for dep in forward.get(node.address, set()):
                    if dep not in changed_addrs:
                        extra_seeds.add(dep)

        has_boundary = any(is_boundary_resource(n.resource_type) for n in nodes)
        has_destructive = any(n.action in ("delete", "replace") for n in nodes)

        bfs_starts = [n.address for n in nodes] + list(extra_seeds)
        all_ar = compute_affected_set(bfs_starts, reverse, max_depth=max_depth)
        l1_affected = [ar for ar in all_ar if ar.depth == 1]
        l2_affected = [ar for ar in all_ar if ar.depth >= 2]
        if not (has_boundary or has_destructive):
            l2_affected = []
        all_affected = [
            ar for ar in l1_affected + l2_affected
            if not _addr_in_plan(ar.address, changed_addrs)
        ]
        l1_affected = [ar for ar in all_affected if ar.depth == 1]
        l2_affected = [ar for ar in all_affected if ar.depth >= 2]

    # Unified smart matching — replaces both history_prior and resource_warnings
    relevant_fixes = find_relevant_fixes(nodes, repo, max_total=max_resource_warnings)

    # Compute history count for blast score — only qualifying matches
    qualifying_fixes = []
    for rf in relevant_fixes:
        conf = rf.get("confidence", "low")
        if conf == "high":
            qualifying_fixes.append(rf)
        elif conf == "medium":
            supporting = rf.get("match_reason", {}).get("supporting_signals", [])
            if len(supporting) >= 1:
                qualifying_fixes.append(rf)
    history_count = min(len(qualifying_fixes[:3]), 3)

    # Backward compat: populate legacy fields from relevant_fixes
    resource_warnings = relevant_fixes
    history_matches = [
        {"id": rf["short_id"], "issue": rf["issue"],
         "resource_type": rf.get("match_reason", {}).get("resource_type", "")}
        for rf in qualifying_fixes[:3]
    ]

    # Blast score
    changed_addresses = {n.address for n in nodes}
    l1_score_count = sum(1 for ar in l1_affected if not _addr_in_plan(ar.address, changed_addresses))
    l2_score_count = sum(1 for ar in l2_affected if not _addr_in_plan(ar.address, changed_addresses))

    score = compute_blast_score(
        nodes, l1_score_count, l2_score_count, history_count
    )
    sev = severity_label(score)

    # Score explanation bullets
    explanation = build_score_explanation(nodes, l1_score_count, l2_score_count, history_count)
    score_explanation = [
        {"label": e.label, "delta": e.delta, "kind": e.kind} for e in explanation
    ]

    # Contextual checks
    ctx_checks = generate_contextual_checks(nodes, relevant_fixes)
    # Legacy checks field: flat list of check strings
    checks = [c["check"] for c in ctx_checks]

    # Build why-paths for affected resources
    why_paths = []
    for ar in all_affected[:20]:
        why_paths.append(
            {
                "target": ar.address,
                "depth": ar.depth,
                "path": ar.path,
            }
        )

    # Plan summary
    plan_summary = {
        "total_changes": len(nodes),
        "control_points": len(control_points),
        "affected_resources": len(all_affected),
        "by_action": {},
    }
    for n in nodes:
        plan_summary["by_action"][n.action] = (
            plan_summary["by_action"].get(n.action, 0) + 1
        )

    return BlastResult(
        score=score,
        severity=sev,
        changes=changes,
        control_points=[
            {
                "address": cp.address,
                "resource_type": cp.resource_type,
                "action": cp.action,
                "category": cp.category,
                "criticality": cp.criticality,
            }
            for cp in control_points
        ],
        affected=[
            {
                "address": ar.address,
                "depth": ar.depth,
                "path": ar.path,
            }
            for ar in all_affected
        ],
        why_paths=why_paths,
        checks=checks,
        history_matches=history_matches,
        plan_summary=plan_summary,
        resource_warnings=resource_warnings,
        score_explanation=score_explanation,
        relevant_fixes=relevant_fixes,
        contextual_checks=ctx_checks,
    )


def _find_change_block(plan: dict, address: str) -> Optional[dict]:
    """Find the change block for a resource address in the plan."""
    for change in plan.get("resource_changes", []):
        if change.get("address") == address:
            return change.get("change", {})
    return None
