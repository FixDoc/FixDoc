"""Tests for the fixdoc blast-radius feature."""

import importlib
import json
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from fixdoc.blast_radius import (
    classify_control_point,
    is_boundary_resource,
    parse_dot_graph,
    compute_affected_set,
    compute_tiered_affected,
    compute_blast_score,
    severity_label,
    build_score_explanation,
    compute_history_prior,
    redact_plan_values,
    generate_checks,
    analyze_blast_radius,
    find_resource_prior_fixes,
    is_actionable_change,
    BlastNode,
    BlastResult,
    ScoreExplanation,
    _normalize_tf_node,
    _normalize_action,
    _history_cluster_key,
    _dedup_history_candidates,
    _extract_principals,
    _compute_iam_sensitivity,
    ACTION_POINTS,
)
from fixdoc.cli import create_cli
from fixdoc.config import FixDocConfig
from fixdoc.models import Fix
from fixdoc.storage import FixRepository

# Get the actual command module for subprocess patching
_analyze_cmd_mod = importlib.import_module("fixdoc.commands.analyze")


def make_obj(tmp_path):
    """Create a ctx.obj dict for test invocations."""
    return {
        "base_path": tmp_path,
        "config": FixDocConfig(),
        "config_manager": MagicMock(),
    }


def make_plan(resource_changes=None):
    """Create a minimal Terraform plan dict."""
    return {"resource_changes": resource_changes or []}


def make_resource_change(address, resource_type, actions, provider_name=""):
    """Create a resource_change entry for a plan."""
    return {
        "address": address,
        "type": resource_type,
        "name": address.split(".")[-1] if "." in address else address,
        "provider_name": provider_name,
        "change": {
            "actions": actions,
            "after": {},
        },
    }


# ===================================================================
# TestControlPointClassification
# ===================================================================


class TestControlPointClassification:
    """Tests for control-point prefix matching and criticality."""

    def test_aws_iam_role(self):
        result = classify_control_point("aws_iam_role")
        assert result is not None
        assert result[0] == "iam"
        assert result[1] == 0.9

    def test_aws_iam_policy(self):
        result = classify_control_point("aws_iam_policy")
        assert result is not None
        assert result[0] == "iam"

    def test_aws_iam_role_policy_attachment(self):
        result = classify_control_point("aws_iam_role_policy_attachment")
        assert result is not None
        assert result[0] == "iam"
        assert result[1] == 0.9

    def test_azure_role_assignment(self):
        result = classify_control_point("azurerm_role_assignment")
        assert result is not None
        assert result[0] == "rbac"
        assert result[1] == 0.9

    def test_gcp_iam_prefix_match(self):
        """google_project_iam_member matches google_project_iam prefix."""
        result = classify_control_point("google_project_iam_member")
        assert result is not None
        assert result[0] == "iam"
        assert result[1] == 0.9

    def test_gcp_service_account(self):
        result = classify_control_point("google_service_account")
        assert result is not None
        assert result[0] == "iam"

    def test_network_security_group(self):
        result = classify_control_point("aws_security_group")
        assert result is not None
        assert result[0] == "network"
        assert result[1] == 0.8

    def test_non_control_point_returns_none(self):
        result = classify_control_point("aws_s3_bucket")
        assert result is None

    def test_case_insensitive(self):
        result = classify_control_point("AWS_IAM_ROLE")
        assert result is not None
        assert result[0] == "iam"

    def test_is_boundary_resource(self):
        assert is_boundary_resource("aws_iam_role") is True
        assert is_boundary_resource("aws_s3_bucket") is False


# ===================================================================
# TestNormalizeAction
# ===================================================================


class TestNormalizeAction:
    """Tests for action normalization."""

    def test_create_delete_is_replace(self):
        assert _normalize_action(["create", "delete"]) == "replace"

    def test_delete_only(self):
        assert _normalize_action(["delete"]) == "delete"

    def test_update_only(self):
        assert _normalize_action(["update"]) == "update"

    def test_create_only(self):
        assert _normalize_action(["create"]) == "create"

    def test_no_op(self):
        assert _normalize_action(["no-op"]) == "no-op"


# ===================================================================
# TestDotParser
# ===================================================================


class TestDotParser:
    """Tests for DOT graph parsing and node normalization."""

    def test_simple_quoted_edge(self):
        dot = '"aws_iam_role.app" -> "aws_lambda_function.api"'
        fwd, rev = parse_dot_graph(dot)
        assert "aws_lambda_function.api" in fwd.get("aws_iam_role.app", set())

    def test_unquoted_edge(self):
        dot = "nodeA -> nodeB"
        fwd, rev = parse_dot_graph(dot)
        assert "nodeB" in fwd.get("nodeA", set())

    def test_reverse_adjacency(self):
        dot = '"A" -> "B"'
        fwd, rev = parse_dot_graph(dot)
        assert "A" in rev.get("B", set())

    def test_normalize_root_prefix(self):
        assert _normalize_tf_node("[root] aws_iam_role.app") == "aws_iam_role.app"

    def test_normalize_expand_suffix(self):
        assert _normalize_tf_node("[root] module.app (expand)") == "module.app"

    def test_normalize_close_suffix(self):
        assert _normalize_tf_node("[root] module.app (close)") == "module.app"

    def test_ignores_comments(self):
        dot = '// this is a comment\n"A" -> "B"'
        fwd, rev = parse_dot_graph(dot)
        assert "B" in fwd.get("A", set())
        assert len(fwd) == 2  # A and B

    def test_ignores_subgraph(self):
        dot = 'subgraph cluster_0 {\n"A" -> "B"\n}'
        fwd, _ = parse_dot_graph(dot)
        assert "B" in fwd.get("A", set())

    def test_empty_input(self):
        fwd, rev = parse_dot_graph("")
        assert fwd == {}
        assert rev == {}

    def test_real_tf_snippet(self):
        dot = """digraph {
    "[root] aws_iam_role.app" -> "[root] provider.aws"
    "[root] aws_lambda_function.api" -> "[root] aws_iam_role.app"
}"""
        fwd, rev = parse_dot_graph(dot)
        assert "provider.aws" in fwd.get("aws_iam_role.app", set())
        assert "aws_iam_role.app" in fwd.get("aws_lambda_function.api", set())

    def test_multiple_edges_from_same_node(self):
        dot = '"A" -> "B"\n"A" -> "C"'
        fwd, _ = parse_dot_graph(dot)
        assert fwd["A"] == {"B", "C"}


# ===================================================================
# TestBFS
# ===================================================================


class TestBFS:
    """Tests for bounded BFS propagation."""

    def test_single_hop(self):
        adj = {"A": {"B"}, "B": set()}
        result = compute_affected_set(["A"], adj, max_depth=5)
        assert len(result) == 1
        assert result[0].address == "B"
        assert result[0].depth == 1

    def test_multi_hop(self):
        adj = {"A": {"B"}, "B": {"C"}, "C": set()}
        result = compute_affected_set(["A"], adj, max_depth=5)
        addrs = {r.address for r in result}
        assert addrs == {"B", "C"}

    def test_max_depth_respected(self):
        adj = {"A": {"B"}, "B": {"C"}, "C": {"D"}, "D": set()}
        result = compute_affected_set(["A"], adj, max_depth=2)
        addrs = {r.address for r in result}
        assert "B" in addrs
        assert "C" in addrs
        assert "D" not in addrs

    def test_cycle_safety(self):
        adj = {"A": {"B"}, "B": {"C"}, "C": {"A"}}
        result = compute_affected_set(["A"], adj, max_depth=10)
        addrs = {r.address for r in result}
        assert addrs == {"B", "C"}

    def test_multiple_starts(self):
        adj = {"A": {"C"}, "B": {"C"}, "C": {"D"}, "D": set()}
        result = compute_affected_set(["A", "B"], adj, max_depth=5)
        addrs = {r.address for r in result}
        assert "C" in addrs
        assert "D" in addrs

    def test_disconnected_node(self):
        adj = {"A": {"B"}, "B": set(), "X": {"Y"}, "Y": set()}
        result = compute_affected_set(["A"], adj, max_depth=5)
        addrs = {r.address for r in result}
        assert addrs == {"B"}
        assert "X" not in addrs
        assert "Y" not in addrs


# ===================================================================
# TestTieredAffected
# ===================================================================


class TestTieredAffected:
    """Tests for tiered L1/L2 affected set computation."""

    def test_l2_gated_for_non_boundary_updates(self):
        """Non-boundary update should not propagate past depth 1."""
        nodes = [BlastNode("aws_s3_bucket.data", "aws_s3_bucket", "update")]
        adj = {
            "aws_s3_bucket.data": {"B"},
            "B": {"C"},
            "C": set(),
        }
        l1, l2 = compute_tiered_affected(nodes, adj, max_depth=5)
        assert len(l1) == 1
        assert l1[0].address == "B"
        assert len(l2) == 0  # L2 gated

    def test_l2_included_for_boundary_update(self):
        """Boundary resource update should include L2."""
        nodes = [BlastNode("aws_security_group.main", "aws_security_group", "update")]
        adj = {
            "aws_security_group.main": {"B"},
            "B": {"C"},
            "C": set(),
        }
        l1, l2 = compute_tiered_affected(nodes, adj, max_depth=5)
        assert len(l1) == 1
        assert len(l2) == 1
        assert l2[0].address == "C"

    def test_l2_included_for_delete(self):
        """Delete action should include L2."""
        nodes = [BlastNode("aws_s3_bucket.data", "aws_s3_bucket", "delete")]
        adj = {
            "aws_s3_bucket.data": {"B"},
            "B": {"C"},
            "C": set(),
        }
        l1, l2 = compute_tiered_affected(nodes, adj, max_depth=5)
        assert len(l1) == 1
        assert len(l2) == 1


# ===================================================================
# TestBlastScore
# ===================================================================


class TestBlastScore:
    """Tests for linear blast score computation and severity labeling."""

    def test_zero_baseline(self):
        """No changed nodes gives zero score."""
        score = compute_blast_score([], 0, 0, 0)
        assert score == 0

    def test_single_update_non_boundary(self):
        """Single non-boundary update: 5 points."""
        nodes = [BlastNode("aws_s3_bucket.data", "aws_s3_bucket", "update")]
        score = compute_blast_score(nodes, 0, 0, 0)
        assert score == 5.0

    def test_single_sg_update_2_dependents(self):
        """In-place SG update + 2 dependents → LOW."""
        nodes = [BlastNode("aws_security_group.cache", "aws_security_group", "update")]
        score = compute_blast_score(nodes, 2, 0, 0)
        # 5 * 1.5 (boundary) + 2 * 1.5 (boundary not all_updates_no_boundary) = 7.5 + 3 = 10.5
        assert score < 25
        assert severity_label(score) == "low"

    def test_delete_iam_role_7_dependents(self):
        """Delete IAM role + 7 dependents → MEDIUM."""
        nodes = [BlastNode("aws_iam_role.app", "aws_iam_role", "delete")]
        score = compute_blast_score(nodes, 3, 4, 0)
        # 20 * 1.5 (boundary) + 7 * 1.5 = 30 + 10.5 = 40.5
        assert 25 <= score < 75
        assert severity_label(score) == "medium"

    def test_delete_higher_than_update(self):
        nodes_del = [BlastNode("aws_s3_bucket.data", "aws_s3_bucket", "delete")]
        nodes_upd = [BlastNode("aws_s3_bucket.data", "aws_s3_bucket", "update")]
        del_score = compute_blast_score(nodes_del, 0, 0, 0)
        upd_score = compute_blast_score(nodes_upd, 0, 0, 0)
        assert del_score > upd_score

    def test_history_increases_score(self):
        nodes = [BlastNode("aws_s3_bucket.data", "aws_s3_bucket", "update")]
        no_hist = compute_blast_score(nodes, 0, 0, 0)
        with_hist = compute_blast_score(nodes, 0, 0, 3)
        assert with_hist > no_hist

    def test_history_capped_at_15(self):
        nodes = [BlastNode("aws_s3_bucket.data", "aws_s3_bucket", "update")]
        score_3 = compute_blast_score(nodes, 0, 0, 3)
        score_10 = compute_blast_score(nodes, 0, 0, 10)
        # Both should have same history contribution (capped at 15)
        assert score_3 == score_10

    def test_impacted_count_capped_at_25(self):
        nodes = [BlastNode("aws_iam_role.app", "aws_iam_role", "delete")]
        score_25 = compute_blast_score(nodes, 25, 0, 0)
        score_50 = compute_blast_score(nodes, 25, 25, 0)
        # L1+L2 capped at 25
        assert score_25 == score_50

    def test_impact_multiplier_low_for_non_boundary_updates(self):
        """All updates non-boundary → impact_multiplier = 0.5."""
        nodes = [BlastNode("aws_s3_bucket.data", "aws_s3_bucket", "update")]
        score = compute_blast_score(nodes, 5, 0, 0)
        # 5 (update) + 5 * 0.5 (low multiplier) = 7.5
        assert score == 7.5

    def test_severity_critical(self):
        assert severity_label(80) == "critical"

    def test_severity_high(self):
        assert severity_label(55) == "high"

    def test_severity_medium(self):
        assert severity_label(30) == "medium"

    def test_severity_low(self):
        assert severity_label(20) == "low"

    def test_severity_boundaries(self):
        assert severity_label(75) == "critical"
        assert severity_label(74.9) == "high"
        assert severity_label(50) == "high"
        assert severity_label(49.9) == "medium"
        assert severity_label(25) == "medium"
        assert severity_label(24.9) == "low"

    def test_replace_action(self):
        """Replace (create+delete) is worth 25 points."""
        nodes = [BlastNode("aws_s3_bucket.data", "aws_s3_bucket", "replace")]
        score = compute_blast_score(nodes, 0, 0, 0)
        assert score == 25.0

    def test_single_create_non_boundary(self):
        """Single non-boundary create gets greenfield discount: 8 * 0.3 = 2.4."""
        nodes = [BlastNode("aws_s3_bucket.data", "aws_s3_bucket", "create")]
        score = compute_blast_score(nodes, 0, 0, 0)
        assert score == 2.4

    def test_boundary_create_greenfield_smaller_discount(self):
        """Boundary create in greenfield gets smaller discount than non-boundary: 8*1.5*0.5=6.0 vs 8*0.3=2.4."""
        node_boundary = [BlastNode("aws_iam_role.app", "aws_iam_role", "create")]
        node_plain = [BlastNode("aws_s3_bucket.data", "aws_s3_bucket", "create")]
        score_boundary = compute_blast_score(node_boundary, 0, 0, 0)
        score_plain = compute_blast_score(node_plain, 0, 0, 0)
        assert score_boundary == 6.0   # 8 * 1.5 * 0.5
        assert score_plain == 2.4      # 8 * 0.3
        assert score_boundary > score_plain

    def test_greenfield_many_creates_under_100(self):
        """13 all-create resources should NOT score 100 (greenfield discount)."""
        nodes = [
            BlastNode(f"aws_instance.app_{i}", "aws_instance", "create")
            for i in range(13)
        ]
        score = compute_blast_score(nodes, 0, 0, 0)
        # 13 * 8 * 0.4 = 41.6 — well under 100
        # Severity thresholds: >=75 critical, >=50 high, >=25 medium
        assert score < 75
        assert severity_label(score) == "medium"

    def test_greenfield_lower_than_equivalent_update(self):
        """Greenfield plan scores lower than same resources with one update mixed in."""
        nodes_create = [
            BlastNode("aws_s3_bucket.data", "aws_s3_bucket", "create"),
            BlastNode("aws_instance.app", "aws_instance", "create"),
        ]
        nodes_mixed = [
            BlastNode("aws_s3_bucket.data", "aws_s3_bucket", "update"),  # not greenfield
            BlastNode("aws_instance.app", "aws_instance", "create"),
        ]
        score_create = compute_blast_score(nodes_create, 5, 0, 0)
        score_mixed = compute_blast_score(nodes_mixed, 5, 0, 0)
        assert score_create < score_mixed

    def test_greenfield_50_boundary_creates_caps_at_medium(self):
        """50 boundary creates (scenario-16 case) must not score CRITICAL."""
        nodes = (
            [BlastNode(f"aws_iam_role.r_{i}", "aws_iam_role", "create") for i in range(50)]
            + [BlastNode(f"aws_security_group.sg_{i}", "aws_security_group", "create") for i in range(50)]
        )
        score = compute_blast_score(nodes, 0, 0, 0)
        assert score <= 45.0
        assert severity_label(score) == "medium"

    def test_greenfield_cap_does_not_apply_with_external_dependents(self):
        """If a greenfield plan has L1 cross-boundary dependents, no cap — can score higher."""
        nodes = [BlastNode(f"aws_iam_role.r_{i}", "aws_iam_role", "create") for i in range(5)]
        score_no_deps = compute_blast_score(nodes, 0, 0, 0)
        score_with_deps = compute_blast_score(nodes, 10, 0, 0)
        assert score_no_deps <= 45.0   # cap applies when no external deps
        assert score_with_deps > score_no_deps   # external deps push it higher


def test_greenfield_dot_indexed_resource_name_mismatch_caps_at_medium(tmp_path):
    """DOT graph nodes for count resources (aws_sg.bulk) must be treated as
    in-plan when the plan has aws_sg.bulk[0..N], so the greenfield cap applies."""
    repo = FixRepository(tmp_path)
    plan = make_plan([
        make_resource_change("aws_security_group.bulk[0]", "aws_security_group", ["create"]),
        make_resource_change("aws_security_group.bulk[1]", "aws_security_group", ["create"]),
        make_resource_change("aws_security_group.bulk[2]", "aws_security_group", ["create"]),
        make_resource_change("aws_vpc.main", "aws_vpc", ["create"]),
    ])
    # terraform graph emits the base name without index brackets for count resources
    dot_text = '''digraph {
        "aws_security_group.bulk" -> "aws_vpc.main"
    }'''
    result = analyze_blast_radius(plan, dot_text=dot_text, repo=repo)
    assert result.score <= 45.0, f"Expected MEDIUM cap, got {result.score}"
    assert result.severity != "critical"


# ===================================================================
# TestRedaction
# ===================================================================


class TestRedaction:
    """Tests for plan value redaction."""

    def test_password_key_redacted(self):
        change = {"after": {"db_password": "secret123", "name": "mydb"}}
        result = redact_plan_values(change)
        assert result["after"]["db_password"] == "[REDACTED]"
        assert result["after"]["name"] == "mydb"

    def test_token_key_redacted(self):
        change = {"after": {"api_token": "tok_abc", "region": "us-east-1"}}
        result = redact_plan_values(change)
        assert result["after"]["api_token"] == "[REDACTED]"
        assert result["after"]["region"] == "us-east-1"

    def test_sensitive_values_markers(self):
        change = {
            "after": {"connection_string": "postgres://...", "name": "mydb"},
            "after_sensitive": {"connection_string": True},
        }
        result = redact_plan_values(change)
        assert result["after"]["connection_string"] == "[REDACTED]"
        assert result["after"]["name"] == "mydb"

    def test_nested_values(self):
        change = {
            "after": {
                "config": {"secret_key": "abc", "timeout": 30}
            }
        }
        result = redact_plan_values(change)
        assert result["after"]["config"]["secret_key"] == "[REDACTED]"
        assert result["after"]["config"]["timeout"] == 30

    def test_non_sensitive_preserved(self):
        change = {"after": {"name": "myapp", "region": "us-west-2"}}
        result = redact_plan_values(change)
        assert result["after"]["name"] == "myapp"
        assert result["after"]["region"] == "us-west-2"


# ===================================================================
# TestHistoryPrior
# ===================================================================


class TestHistoryPrior:
    """Tests for history-prior scoring."""

    def test_matching_fixes(self, tmp_path):
        """Boundary node + category-tagged fix → match returned."""
        repo = FixRepository(tmp_path)
        node = BlastNode("aws_iam_role.app", "aws_iam_role", "update")
        repo.save(Fix(issue="IAM role issue", resolution="Fixed it",
                      tags="aws_iam_role, iam"))
        count, matches = compute_history_prior(["aws_iam_role"], [node], repo)
        assert count > 0
        assert len(matches) == 1

    def test_no_matches(self, tmp_path):
        """Empty repo → zero matches regardless of nodes."""
        repo = FixRepository(tmp_path)
        node = BlastNode("aws_s3_bucket.data", "aws_s3_bucket", "update")
        count, matches = compute_history_prior(["aws_s3_bucket"], [node], repo)
        assert count == 0
        assert len(matches) == 0

    def test_count_is_exact(self, tmp_path):
        """3 fixes with distinct cluster keys under boundary gate → exactly 3 returned."""
        repo = FixRepository(tmp_path)
        node = BlastNode("aws_iam_role.app", "aws_iam_role", "update")
        for issue in (
            "timeout connecting to iam service",
            "permission denied on role attach",
            "role policy limit exceeded check",
        ):
            repo.save(Fix(issue=issue, resolution="Fix", tags="aws_iam_role, iam"))
        count, matches = compute_history_prior(["aws_iam_role"], [node], repo)
        assert count == 3

    def test_multiple_resource_types(self, tmp_path):
        """Two boundary nodes, two category-tagged fixes → 2 matches."""
        repo = FixRepository(tmp_path)
        nodes = [
            BlastNode("aws_iam_role.app", "aws_iam_role", "delete"),
            BlastNode("aws_security_group.web", "aws_security_group", "update"),
        ]
        repo.save(Fix(issue="IAM issue", resolution="Fix",
                      tags="aws_iam_role, iam"))
        repo.save(Fix(issue="SG issue", resolution="Fix",
                      tags="aws_security_group, networking"))
        count, matches = compute_history_prior(
            ["aws_iam_role", "aws_security_group"], nodes, repo
        )
        assert len(matches) == 2

    # -----------------------------------------------------------------------
    # New tests
    # -----------------------------------------------------------------------

    def test_no_history_for_plain_updates_no_address_match(self, tmp_path):
        """Non-boundary update + no address in fix text → no matches (gate closed)."""
        repo = FixRepository(tmp_path)
        node = BlastNode("aws_instance.app_a", "aws_instance", "update")
        repo.save(Fix(issue="instance type issue", resolution="Fix",
                      tags="aws_instance, storage"))
        count, matches = compute_history_prior(["aws_instance"], [node], repo)
        assert count == 0
        assert matches == []

    def test_address_override_for_plain_update(self, tmp_path):
        """Fix whose issue text contains the changed address surfaces via Phase 1."""
        repo = FixRepository(tmp_path)
        node = BlastNode("aws_instance.app_a", "aws_instance", "update")
        repo.save(Fix(
            issue="aws_instance.app_a ran out of capacity",
            resolution="Changed AZ",
            tags="aws_instance",
        ))
        count, matches = compute_history_prior(["aws_instance"], [node], repo)
        assert count == 1
        assert len(matches) == 1

    def test_category_tag_filter_excludes_resource_type_only_tagged_fixes(self, tmp_path):
        """Fix tagged only with resource-type (no category tag) is excluded even under gate."""
        repo = FixRepository(tmp_path)
        node = BlastNode("aws_security_group.web", "aws_security_group", "update")
        repo.save(Fix(issue="sg update failed", resolution="Fix",
                      tags="aws_security_group"))
        count, matches = compute_history_prior(["aws_security_group"], [node], repo)
        assert count == 0
        assert matches == []

    def test_dedup_most_complete_wins(self, tmp_path):
        """Two fixes with same cluster key → the one with error_excerpt wins."""
        repo = FixRepository(tmp_path)
        node = BlastNode("aws_security_group.web", "aws_security_group", "update")
        fix_no_excerpt = Fix(
            issue="SecurityGroupUpdateFailed rule conflict",
            resolution="Fixed it",
            tags="aws_security_group, networking",
        )
        fix_with_excerpt = Fix(
            issue="SecurityGroupUpdateFailed rule conflict",
            resolution="Fixed it",
            tags="aws_security_group, networking",
            error_excerpt="sg rule conflict: port 443",
        )
        repo.save(fix_no_excerpt)
        repo.save(fix_with_excerpt)
        count, matches = compute_history_prior(["aws_security_group"], [node], repo)
        assert count == 1
        assert matches[0]["id"] == fix_with_excerpt.id[:8]

    def test_cap_at_3_after_dedup(self, tmp_path):
        """5 fixes with distinct cluster keys, all category-tagged → capped at 3."""
        repo = FixRepository(tmp_path)
        node = BlastNode("aws_security_group.web", "aws_security_group", "update")
        for name in ("TimeoutError", "ConnectError", "QuotaError", "AuthError", "NetworkError"):
            repo.save(Fix(
                issue=f"{name} on security group update",
                resolution="Fixed it",
                tags="aws_security_group, networking",
            ))
        count, matches = compute_history_prior(["aws_security_group"], [node], repo)
        assert count == 3
        assert len(matches) == 3


# ===================================================================
# TestGenerateChecks
# ===================================================================


class TestGenerateChecks:
    """Tests for recommended check generation."""

    def test_iam_checks(self):
        cps = [BlastNode("a", "aws_iam_role", "delete", category="iam", criticality=0.9)]
        checks = generate_checks(cps, has_deletes=False)
        assert any("IAM" in c for c in checks)

    def test_network_checks(self):
        cps = [BlastNode("a", "aws_security_group", "update", category="network", criticality=0.8)]
        checks = generate_checks(cps, has_deletes=False)
        assert any("security group" in c for c in checks)

    def test_delete_check_added(self):
        cps = [BlastNode("a", "aws_iam_role", "delete", category="iam", criticality=0.9)]
        checks = generate_checks(cps, has_deletes=True)
        assert any("not referenced" in c for c in checks)

    def test_no_duplicates_same_category(self):
        cps = [
            BlastNode("a", "aws_iam_role", "delete", category="iam", criticality=0.9),
            BlastNode("b", "aws_iam_policy", "update", category="iam", criticality=0.85),
        ]
        checks = generate_checks(cps, has_deletes=False)
        iam_checks = [c for c in checks if "IAM" in c]
        # Should only have 2 IAM checks (not 4 from duplicate category)
        assert len(iam_checks) == 1


# ===================================================================
# TestAnalyzeCommand (replaces TestBlastRadiusCommand)
# ===================================================================


class TestAnalyzeCommand:
    """Tests for the merged analyze CLI command."""

    def _write_plan(self, tmp_path, plan_data):
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan_data))
        return str(plan_file)

    def test_human_format_output(self, tmp_path):
        plan = make_plan([
            make_resource_change("aws_iam_role.app", "aws_iam_role", ["delete"]),
        ])
        plan_file = self._write_plan(tmp_path, plan)

        runner = CliRunner()
        cli = create_cli()

        with patch.object(_analyze_cmd_mod, "_auto_run_terraform_graph", return_value=None):
            result = runner.invoke(
                cli,
                ["analyze", plan_file],
                obj=make_obj(tmp_path),
            )

        assert result.exit_code == 0
        assert "Terraform Plan Analysis" in result.output
        assert "Risk Score:" in result.output

    def test_json_format_output(self, tmp_path):
        plan = make_plan([
            make_resource_change("aws_iam_role.app", "aws_iam_role", ["delete"]),
        ])
        plan_file = self._write_plan(tmp_path, plan)

        runner = CliRunner(mix_stderr=False)
        cli = create_cli()

        with patch.object(_analyze_cmd_mod, "_auto_run_terraform_graph", return_value=None):
            result = runner.invoke(
                cli,
                ["analyze", plan_file, "--format", "json"],
                obj=make_obj(tmp_path),
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "score" in data
        assert "severity" in data
        assert "control_points" in data

    def test_no_changes_message(self, tmp_path):
        """Plan with only no-op changes shows no-changes message."""
        plan = make_plan([
            make_resource_change("aws_s3_bucket.data", "aws_s3_bucket", ["no-op"]),
        ])
        plan_file = self._write_plan(tmp_path, plan)

        runner = CliRunner()
        cli = create_cli()

        with patch.object(_analyze_cmd_mod, "_auto_run_terraform_graph", return_value=None):
            result = runner.invoke(
                cli,
                ["analyze", plan_file],
                obj=make_obj(tmp_path),
            )

        assert result.exit_code == 0
        assert "No changes to analyze" in result.output

    def test_graph_flag(self, tmp_path):
        plan = make_plan([
            make_resource_change("aws_iam_role.app", "aws_iam_role", ["delete"]),
            make_resource_change("aws_lambda_function.api", "aws_lambda_function", ["update"]),
        ])
        plan_file = self._write_plan(tmp_path, plan)

        dot_file = tmp_path / "graph.dot"
        dot_file.write_text('"aws_iam_role.app" -> "aws_lambda_function.api"')

        runner = CliRunner()
        cli = create_cli()

        result = runner.invoke(
            cli,
            ["analyze", plan_file, "--graph", str(dot_file)],
            obj=make_obj(tmp_path),
        )

        assert result.exit_code == 0

    def test_auto_terraform_graph(self, tmp_path):
        plan = make_plan([
            make_resource_change("aws_iam_role.app", "aws_iam_role", ["delete"]),
        ])
        plan_file = self._write_plan(tmp_path, plan)

        runner = CliRunner()
        cli = create_cli()

        with patch.object(_analyze_cmd_mod, "_auto_run_terraform_graph", return_value='"A" -> "B"'):
            result = runner.invoke(
                cli,
                ["analyze", plan_file],
                obj=make_obj(tmp_path),
            )

        assert result.exit_code == 0

    def test_terraform_not_on_path(self, tmp_path):
        plan = make_plan([
            make_resource_change("aws_iam_role.app", "aws_iam_role", ["delete"]),
        ])
        plan_file = self._write_plan(tmp_path, plan)

        runner = CliRunner()
        cli = create_cli()

        with patch.object(_analyze_cmd_mod, "_auto_run_terraform_graph", return_value=None):
            result = runner.invoke(
                cli,
                ["analyze", plan_file],
                obj=make_obj(tmp_path),
            )

        assert result.exit_code == 0

    def test_invalid_json(self, tmp_path):
        plan_file = tmp_path / "bad.json"
        plan_file.write_text("not json at all {{{")

        runner = CliRunner()
        cli = create_cli()

        result = runner.invoke(
            cli,
            ["analyze", str(plan_file)],
            obj=make_obj(tmp_path),
        )

        assert result.exit_code == 1

    def test_max_depth_option(self, tmp_path):
        plan = make_plan([
            make_resource_change("aws_iam_role.app", "aws_iam_role", ["delete"]),
        ])
        plan_file = self._write_plan(tmp_path, plan)

        runner = CliRunner()
        cli = create_cli()

        with patch.object(_analyze_cmd_mod, "_auto_run_terraform_graph", return_value=None):
            result = runner.invoke(
                cli,
                ["analyze", plan_file, "--max-depth", "2"],
                obj=make_obj(tmp_path),
            )

        assert result.exit_code == 0

    def test_summary_flag(self, tmp_path):
        plan = make_plan([
            make_resource_change("aws_iam_role.app", "aws_iam_role", ["delete"]),
        ])
        plan_file = self._write_plan(tmp_path, plan)

        runner = CliRunner()
        cli = create_cli()

        with patch.object(_analyze_cmd_mod, "_auto_run_terraform_graph", return_value=None):
            result = runner.invoke(
                cli,
                ["analyze", plan_file, "--summary"],
                obj=make_obj(tmp_path),
            )

        assert result.exit_code == 0
        assert "Risk:" in result.output

    def test_match_flag_strict(self, tmp_path):
        plan = make_plan([
            make_resource_change("aws_iam_role.app", "aws_iam_role", ["delete"]),
        ])
        plan_file = self._write_plan(tmp_path, plan)

        runner = CliRunner()
        cli = create_cli()

        with patch.object(_analyze_cmd_mod, "_auto_run_terraform_graph", return_value=None):
            result = runner.invoke(
                cli,
                ["analyze", plan_file, "--match", "strict"],
                obj=make_obj(tmp_path),
            )

        assert result.exit_code == 0

    def test_replace_action_detected(self, tmp_path):
        """create+delete is detected as replace."""
        plan = make_plan([
            make_resource_change("aws_s3_bucket.data", "aws_s3_bucket", ["create", "delete"]),
        ])
        plan_file = self._write_plan(tmp_path, plan)

        runner = CliRunner(mix_stderr=False)
        cli = create_cli()

        with patch.object(_analyze_cmd_mod, "_auto_run_terraform_graph", return_value=None):
            result = runner.invoke(
                cli,
                ["analyze", plan_file, "--format", "json"],
                obj=make_obj(tmp_path),
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["changes"][0]["action"] == "replace"


# ===================================================================
# TestExitOnFlag
# ===================================================================


class TestExitOnFlag:
    """Tests for the --exit-on CI gating flag."""

    def _write_plan(self, tmp_path, plan_data):
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan_data))
        return str(plan_file)

    def test_exit_on_not_provided_exits_zero(self, tmp_path):
        """Without --exit-on, command always exits 0."""
        plan = make_plan([
            make_resource_change("aws_iam_role.app", "aws_iam_role", ["delete"]),
        ])
        plan_file = self._write_plan(tmp_path, plan)

        runner = CliRunner()
        cli = create_cli()

        with patch.object(_analyze_cmd_mod, "_auto_run_terraform_graph", return_value=None):
            result = runner.invoke(
                cli,
                ["analyze", plan_file],
                obj=make_obj(tmp_path),
            )

        assert result.exit_code == 0

    def test_exit_on_low_triggers_on_any_change(self, tmp_path):
        """--exit-on low triggers exit 1 for any non-trivial change."""
        plan = make_plan([
            make_resource_change("aws_iam_role.app", "aws_iam_role", ["delete"]),
        ])
        plan_file = self._write_plan(tmp_path, plan)

        runner = CliRunner()
        cli = create_cli()

        with patch.object(_analyze_cmd_mod, "_auto_run_terraform_graph", return_value=None):
            result = runner.invoke(
                cli,
                ["analyze", plan_file, "--exit-on", "low"],
                obj=make_obj(tmp_path),
            )

        # IAM delete: 20 * 1.5 = 30 → medium, which is >= low
        assert result.exit_code == 1

    def test_exit_on_critical_passes_for_low_score(self, tmp_path):
        """--exit-on critical passes for a low-severity change."""
        plan = make_plan([
            make_resource_change("aws_s3_bucket.data", "aws_s3_bucket", ["create"]),
        ])
        plan_file = self._write_plan(tmp_path, plan)

        runner = CliRunner()
        cli = create_cli()

        with patch.object(_analyze_cmd_mod, "_auto_run_terraform_graph", return_value=None):
            result = runner.invoke(
                cli,
                ["analyze", plan_file, "--exit-on", "critical"],
                obj=make_obj(tmp_path),
            )

        assert result.exit_code == 0

    def test_exit_on_still_prints_output(self, tmp_path):
        """Output is printed before exit 1."""
        plan = make_plan([
            make_resource_change("aws_iam_role.app", "aws_iam_role", ["delete"]),
        ])
        plan_file = self._write_plan(tmp_path, plan)

        runner = CliRunner()
        cli = create_cli()

        with patch.object(_analyze_cmd_mod, "_auto_run_terraform_graph", return_value=None):
            result = runner.invoke(
                cli,
                ["analyze", plan_file, "--exit-on", "low"],
                obj=make_obj(tmp_path),
            )

        assert result.exit_code == 1
        assert "Terraform Plan Analysis" in result.output
        assert "Risk Score:" in result.output

    def test_exit_on_json_still_prints_output(self, tmp_path):
        """JSON output is printed before exit 1."""
        plan = make_plan([
            make_resource_change("aws_iam_role.app", "aws_iam_role", ["delete"]),
        ])
        plan_file = self._write_plan(tmp_path, plan)

        runner = CliRunner(mix_stderr=False)
        cli = create_cli()

        with patch.object(_analyze_cmd_mod, "_auto_run_terraform_graph", return_value=None):
            result = runner.invoke(
                cli,
                ["analyze", plan_file, "--format", "json", "--exit-on", "low"],
                obj=make_obj(tmp_path),
            )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "score" in data
        assert "severity" in data

    def test_exit_on_invalid_choice(self, tmp_path):
        """Invalid --exit-on value is rejected by Click."""
        plan = make_plan([
            make_resource_change("aws_iam_role.app", "aws_iam_role", ["delete"]),
        ])
        plan_file = self._write_plan(tmp_path, plan)

        runner = CliRunner()
        cli = create_cli()

        with patch.object(_analyze_cmd_mod, "_auto_run_terraform_graph", return_value=None):
            result = runner.invoke(
                cli,
                ["analyze", plan_file, "--exit-on", "extreme"],
                obj=make_obj(tmp_path),
            )

        assert result.exit_code != 0


# ===================================================================
# TestEndToEnd
# ===================================================================


class TestEndToEnd:
    """End-to-end integration tests."""

    def test_iam_delete_scenario(self, tmp_path):
        """Full IAM role deletion with graph and history."""
        repo = FixRepository(tmp_path)
        repo.save(Fix(
            issue="IAM role deletion broke Lambda functions",
            resolution="Recreated role with matching policy",
            tags="aws_iam_role,aws,iam",
        ))

        plan = make_plan([
            make_resource_change("aws_iam_role.app_role", "aws_iam_role", ["delete"]),
            make_resource_change("aws_lambda_function.api", "aws_lambda_function", ["update"]),
        ])

        dot = """digraph {
    "aws_lambda_function.api" -> "aws_iam_role.app_role"
}"""

        result = analyze_blast_radius(plan, repo, dot_text=dot, max_depth=5)

        assert result.score > 0
        assert result.severity in ("low", "medium", "high", "critical")
        assert len(result.control_points) == 1
        assert result.control_points[0]["address"] == "aws_iam_role.app_role"
        assert len(result.history_matches) >= 1
        assert len(result.checks) > 0

    def test_network_change_scenario(self, tmp_path):
        """Security group modification with downstream resources."""
        repo = FixRepository(tmp_path)

        plan = make_plan([
            make_resource_change("aws_security_group.main", "aws_security_group", ["update"]),
            make_resource_change("aws_instance.web", "aws_instance", ["update"]),
        ])

        dot = """digraph {
    "aws_instance.web" -> "aws_security_group.main"
}"""

        result = analyze_blast_radius(plan, repo, dot_text=dot, max_depth=5)

        assert result.score > 0
        assert len(result.control_points) == 1
        assert result.control_points[0]["category"] == "network"
        assert any("security group" in c for c in result.checks)

    def test_iam_policy_propagates_through_role_to_instance(self, tmp_path):
        """IAM policy update should surface instance profile and EC2 as impacted.

        Graph: aws_iam_role_policy.inline -> aws_iam_role.app_role
               aws_iam_instance_profile.profile -> aws_iam_role.app_role
               aws_instance.app -> aws_iam_instance_profile.profile

        Changed: aws_iam_role_policy.inline (update)
        Expected impacted: aws_iam_instance_profile.profile (L1), aws_instance.app (L2)
        """
        repo = FixRepository(tmp_path)
        plan = make_plan([
            make_resource_change(
                "aws_iam_role_policy.inline", "aws_iam_role_policy", ["update"]
            ),
        ])
        dot = """digraph G {
  rankdir = "RL";
  "aws_iam_instance_profile.profile" -> "aws_iam_role.app_role";
  "aws_iam_role_policy.inline" -> "aws_iam_role.app_role";
  "aws_instance.app" -> "aws_iam_instance_profile.profile";
}"""
        result = analyze_blast_radius(plan, repo, dot_text=dot)
        affected_addrs = {a["address"] for a in result.affected}
        assert "aws_iam_instance_profile.profile" in affected_addrs
        assert "aws_instance.app" in affected_addrs
        assert result.score > 7.5  # higher than without graph propagation

    def test_no_changes_scenario(self, tmp_path):
        """Plan with only no-op changes has zero score."""
        repo = FixRepository(tmp_path)

        plan = make_plan([
            make_resource_change("aws_s3_bucket.data", "aws_s3_bucket", ["no-op"]),
        ])

        result = analyze_blast_radius(plan, repo)

        assert result.score == 0
        assert result.severity == "low"
        assert len(result.control_points) == 0


# ===================================================================
# TestIsActionableChange
# ===================================================================


class TestIsActionableChange:
    """Tests for is_actionable_change()."""

    def test_create_is_actionable(self):
        node = BlastNode(address="a", resource_type="aws_s3_bucket", action="create")
        assert is_actionable_change(node) is True

    def test_update_is_actionable(self):
        node = BlastNode(address="a", resource_type="aws_s3_bucket", action="update")
        assert is_actionable_change(node) is True

    def test_delete_is_actionable(self):
        node = BlastNode(address="a", resource_type="aws_s3_bucket", action="delete")
        assert is_actionable_change(node) is True

    def test_replace_is_actionable(self):
        node = BlastNode(address="a", resource_type="aws_s3_bucket", action="replace")
        assert is_actionable_change(node) is True

    def test_noop_not_actionable(self):
        node = BlastNode(address="a", resource_type="aws_s3_bucket", action="no-op")
        assert is_actionable_change(node) is False

    def test_read_not_actionable(self):
        node = BlastNode(address="a", resource_type="aws_s3_bucket", action="read")
        assert is_actionable_change(node) is False

    def test_refresh_only_not_actionable(self):
        node = BlastNode(address="a", resource_type="aws_s3_bucket", action="refresh-only")
        assert is_actionable_change(node) is False

    def test_unknown_not_actionable(self):
        node = BlastNode(address="a", resource_type="aws_s3_bucket", action="unknown")
        assert is_actionable_change(node) is False


# ===================================================================
# TestFindResourcePriorFixes
# ===================================================================


def _make_node(address, resource_type, action="create"):
    return BlastNode(address=address, resource_type=resource_type, action=action)


class TestFindResourcePriorFixes:
    """Tests for find_resource_prior_fixes()."""

    def test_empty_repo_returns_empty(self, tmp_path):
        repo = FixRepository(tmp_path)
        nodes = [_make_node("aws_s3_bucket.data", "aws_s3_bucket")]
        result = find_resource_prior_fixes(nodes, repo)
        assert result == []

    def test_tag_match_returns_score_100(self, tmp_path):
        repo = FixRepository(tmp_path)
        repo.save(Fix(
            issue="S3 bucket ACL error",
            resolution="Set acl to private",
            tags="aws_s3_bucket,storage",
        ))
        nodes = [_make_node("aws_s3_bucket.data", "aws_s3_bucket")]
        result = find_resource_prior_fixes(nodes, repo)
        assert len(result) == 1
        assert result[0]["score"] == 100
        assert result[0]["match_reason"] == "tag_match"

    def test_text_match_returns_score_60(self, tmp_path):
        repo = FixRepository(tmp_path)
        repo.save(Fix(
            issue="aws_s3_bucket versioning broke after update",
            resolution="Re-enabled versioning",
            tags="storage",
        ))
        nodes = [_make_node("aws_s3_bucket.data", "aws_s3_bucket")]
        result = find_resource_prior_fixes(nodes, repo)
        assert len(result) == 1
        assert result[0]["score"] == 60
        assert result[0]["match_reason"] == "text_match"

    def test_substring_not_matched_word_boundary(self, tmp_path):
        """aws_s3_bucket should NOT match aws_s3_bucket_policy via text search."""
        repo = FixRepository(tmp_path)
        repo.save(Fix(
            issue="aws_s3_bucket_policy denied access",
            resolution="Updated bucket policy",
            tags="storage",
        ))
        nodes = [_make_node("aws_s3_bucket.data", "aws_s3_bucket")]
        result = find_resource_prior_fixes(nodes, repo)
        assert result == []

    def test_tag_only_excludes_text_matches(self, tmp_path):
        repo = FixRepository(tmp_path)
        repo.save(Fix(
            issue="aws_s3_bucket versioning error",
            resolution="Fixed versioning",
            tags="storage",
        ))
        nodes = [_make_node("aws_s3_bucket.data", "aws_s3_bucket")]
        result = find_resource_prior_fixes(nodes, repo, tag_only=True)
        assert result == []

    def test_same_fix_two_resources_one_entry(self, tmp_path):
        repo = FixRepository(tmp_path)
        repo.save(Fix(
            issue="S3 bucket ACL error",
            resolution="Set acl to private",
            tags="aws_s3_bucket,storage",
        ))
        nodes = [
            _make_node("aws_s3_bucket.data", "aws_s3_bucket"),
            _make_node("aws_s3_bucket.logs", "aws_s3_bucket"),
        ]
        result = find_resource_prior_fixes(nodes, repo)
        assert len(result) == 1
        assert len(result[0]["matched_resources"]) == 2

    def test_max_total_limits_results(self, tmp_path):
        repo = FixRepository(tmp_path)
        for i in range(5):
            repo.save(Fix(
                issue=f"S3 bucket error {i}",
                resolution=f"Fix {i}",
                tags="aws_s3_bucket",
            ))
        nodes = [_make_node("aws_s3_bucket.data", "aws_s3_bucket")]
        result = find_resource_prior_fixes(nodes, repo, max_total=2)
        assert len(result) == 2

    def test_tag_match_before_text_match(self, tmp_path):
        """tag_match (score=100) fix should sort before text_match (score=60)."""
        repo = FixRepository(tmp_path)
        repo.save(Fix(
            issue="aws_s3_bucket text match only",
            resolution="Fix text",
            tags="storage",
        ))
        repo.save(Fix(
            issue="S3 tag match fix",
            resolution="Fix tag",
            tags="aws_s3_bucket",
        ))
        nodes = [_make_node("aws_s3_bucket.data", "aws_s3_bucket")]
        result = find_resource_prior_fixes(nodes, repo)
        assert result[0]["match_reason"] == "tag_match"
        assert result[1]["match_reason"] == "text_match"

    def test_tie_breaking_same_score_by_created_at_desc_then_id_asc(self, tmp_path):
        """Same score → sorted by created_at DESC then id ASC."""
        import time
        repo = FixRepository(tmp_path)
        # Save two fixes with tag match — will have same score
        fix_a = Fix(issue="First fix", resolution="Res A", tags="aws_s3_bucket")
        time.sleep(0.01)
        fix_b = Fix(issue="Second fix", resolution="Res B", tags="aws_s3_bucket")
        repo.save(fix_a)
        repo.save(fix_b)
        nodes = [_make_node("aws_s3_bucket.data", "aws_s3_bucket")]
        result = find_resource_prior_fixes(nodes, repo)
        # Both have same score; more recent (fix_b) should come first
        assert result[0]["issue"] == "Second fix"
        assert result[1]["issue"] == "First fix"

    def test_noop_node_excluded(self, tmp_path):
        repo = FixRepository(tmp_path)
        repo.save(Fix(
            issue="S3 bucket error",
            resolution="Fix it",
            tags="aws_s3_bucket",
        ))
        nodes = [
            _make_node("aws_s3_bucket.data", "aws_s3_bucket", action="no-op"),
        ]
        result = find_resource_prior_fixes(nodes, repo)
        assert result == []

    def test_list_all_called_once_two_nodes_same_type(self, tmp_path):
        """find_by_resource_type called once (not per node) for 2 nodes of same type."""
        repo = FixRepository(tmp_path)
        nodes = [
            _make_node("aws_s3_bucket.a", "aws_s3_bucket"),
            _make_node("aws_s3_bucket.b", "aws_s3_bucket"),
        ]
        with patch.object(repo, "find_by_resource_type", wraps=repo.find_by_resource_type) as mock_find:
            find_resource_prior_fixes(nodes, repo, tag_only=False)
            # find_by_resource_type deduped by unique rt — called once, not twice
            assert mock_find.call_count == 1

    def test_tag_only_list_all_never_called(self, tmp_path):
        """tag_only=True: find_by_resource_type called once per unique rt."""
        repo = FixRepository(tmp_path)
        nodes = [
            _make_node("aws_s3_bucket.a", "aws_s3_bucket"),
            _make_node("aws_instance.b", "aws_instance"),
        ]
        with patch.object(repo, "find_by_resource_type", wraps=repo.find_by_resource_type) as mock_find:
            find_resource_prior_fixes(nodes, repo, tag_only=True)
            assert mock_find.call_count == 2  # one per unique rt

    def test_punctuation_boundary_matches(self, tmp_path):
        """Word-boundary regex matches resource type adjacent to punctuation."""
        repo = FixRepository(tmp_path)
        repo.save(Fix(
            issue='Created "aws_s3_bucket", but ACL denied',
            resolution="Set correct ACL",
            tags="storage",
        ))
        nodes = [_make_node("aws_s3_bucket.data", "aws_s3_bucket")]
        result = find_resource_prior_fixes(nodes, repo)
        assert len(result) == 1

    def test_match_reason_highest_tier_wins(self, tmp_path):
        """Fix matched as tag_match for one rt stays tag_match even if text_match for another."""
        repo = FixRepository(tmp_path)
        fix = Fix(
            issue="aws_instance configuration issue",
            resolution="Fixed config",
            tags="aws_s3_bucket",
        )
        repo.save(fix)
        nodes = [
            _make_node("aws_s3_bucket.data", "aws_s3_bucket"),  # tag_match for this fix
            _make_node("aws_instance.web", "aws_instance"),      # text_match for this fix
        ]
        result = find_resource_prior_fixes(nodes, repo)
        assert len(result) == 1
        assert result[0]["match_reason"] == "tag_match"
        assert result[0]["score"] == 100


# ===================================================================
# TestAnalyzeBlastRadiusResourceWarnings
# ===================================================================


class TestAnalyzeBlastRadiusResourceWarnings:
    """Tests for resource_warnings in analyze_blast_radius()."""

    def test_resource_warnings_populated(self, tmp_path):
        repo = FixRepository(tmp_path)
        repo.save(Fix(
            issue="S3 bucket creation failed",
            resolution="Fixed IAM policy",
            tags="aws_s3_bucket",
        ))
        plan = make_plan([
            make_resource_change("aws_s3_bucket.data", "aws_s3_bucket", ["create"]),
        ])
        result = analyze_blast_radius(plan, repo)
        assert len(result.resource_warnings) >= 1
        assert result.resource_warnings[0]["match_reason"] in ("tag_match", "text_match")

    def test_tag_only_passed_through(self, tmp_path):
        """tag_only=True excludes text_match fixes."""
        repo = FixRepository(tmp_path)
        repo.save(Fix(
            issue="aws_s3_bucket caused ACL issue",
            resolution="Fixed it",
            tags="storage",
        ))
        plan = make_plan([
            make_resource_change("aws_s3_bucket.data", "aws_s3_bucket", ["create"]),
        ])
        result = analyze_blast_radius(plan, repo, tag_only=True)
        assert result.resource_warnings == []

    def test_max_resource_warnings_respected(self, tmp_path):
        repo = FixRepository(tmp_path)
        for i in range(5):
            repo.save(Fix(
                issue=f"S3 error {i}",
                resolution=f"Fix {i}",
                tags="aws_s3_bucket",
            ))
        plan = make_plan([
            make_resource_change("aws_s3_bucket.data", "aws_s3_bucket", ["create"]),
        ])
        result = analyze_blast_radius(plan, repo, max_resource_warnings=3)
        assert len(result.resource_warnings) <= 3


# ===================================================================
# TestAnalyzeFormatHuman
# ===================================================================


def _make_result_with_warnings(warnings):
    """Build a minimal BlastResult with given resource_warnings."""
    return BlastResult(
        score=8.0,
        severity="low",
        changes=[{"address": "aws_s3_bucket.data", "resource_type": "aws_s3_bucket",
                  "action": "create", "cloud_provider": "aws",
                  "is_control_point": False, "category": "", "criticality": 0.0}],
        plan_summary={"total_changes": 1, "control_points": 0,
                      "affected_resources": 0, "by_action": {"create": 1}},
        resource_warnings=warnings,
    )


def _make_changed():
    from fixdoc.commands.analyze import PlanResource
    from fixdoc.parsers.base import CloudProvider
    return [PlanResource(
        address="aws_s3_bucket.data",
        resource_type="aws_s3_bucket",
        name="data",
        cloud_provider=CloudProvider.AWS,
        action="create",
    )]


class TestAnalyzeFormatHuman:
    """Tests for _format_human() tribal knowledge section."""

    def test_section_rendered_when_warnings_present(self):
        from fixdoc.commands.analyze import _format_human
        warnings = [{
            "id": "abcdef1234567890",
            "short_id": "abcdef12",
            "issue": "S3 bucket ACL error",
            "resolution": "Set to private",
            "tags": "aws_s3_bucket",
            "created_at": "2024-01-15T10:00:00",
            "match_reason": "tag_match",
            "score": 100,
            "matched_resources": [{"address": "aws_s3_bucket.data", "action": "create"}],
        }]
        result = _make_result_with_warnings(warnings)
        output = _format_human(result, _make_changed())
        assert "Prior Issues for Changed Resources" in output
        assert "FIX-abcdef12" in output
        assert "Run `fixdoc show" in output

    def test_multi_resource_applies_to_shown(self):
        from fixdoc.commands.analyze import _format_human
        warnings = [{
            "id": "abcdef1234567890",
            "short_id": "abcdef12",
            "issue": "S3 ACL error",
            "resolution": "Fixed",
            "tags": "",
            "created_at": "2024-01-15",
            "match_reason": "tag_match",
            "score": 100,
            "matched_resources": [
                {"address": "aws_s3_bucket.a", "action": "create"},
                {"address": "aws_s3_bucket.b", "action": "create"},
            ],
        }]
        result = _make_result_with_warnings(warnings)
        output = _format_human(result, _make_changed())
        assert "Applies to:" in output

    def test_single_resource_applies_to_shown(self):
        # "Applies to" is now always shown when matched_resources is non-empty
        from fixdoc.commands.analyze import _format_human
        warnings = [{
            "id": "abcdef1234567890",
            "short_id": "abcdef12",
            "issue": "S3 ACL error",
            "resolution": "Fixed",
            "tags": "",
            "created_at": "2024-01-15",
            "match_reason": "tag_match",
            "score": 100,
            "matched_resources": [
                {"address": "aws_s3_bucket.a", "action": "create"},
            ],
        }]
        result = _make_result_with_warnings(warnings)
        output = _format_human(result, _make_changed())
        assert "Applies to:" in output

    def test_empty_warnings_section_not_rendered(self):
        from fixdoc.commands.analyze import _format_human
        result = _make_result_with_warnings([])
        output = _format_human(result, _make_changed())
        assert "Prior Issues for Changed Resources" not in output

    def test_verbose_shows_score_and_tags(self):
        from fixdoc.commands.analyze import _format_human
        warnings = [{
            "id": "abcdef1234567890",
            "short_id": "abcdef12",
            "issue": "S3 ACL error",
            "resolution": "Set to private",
            "tags": "aws_s3_bucket,storage",
            "created_at": "2024-01-15",
            "match_reason": "tag_match",
            "score": 100,
            "matched_resources": [{"address": "aws_s3_bucket.data", "action": "create"}],
        }]
        result = _make_result_with_warnings(warnings)
        output = _format_human(result, _make_changed(), verbose=True)
        assert "[score:100 | tag_match]" in output
        assert "Tags:" in output

    def test_nonverbose_hides_score_and_tags(self):
        from fixdoc.commands.analyze import _format_human
        warnings = [{
            "id": "abcdef1234567890",
            "short_id": "abcdef12",
            "issue": "S3 ACL error",
            "resolution": "Set to private",
            "tags": "aws_s3_bucket,storage",
            "created_at": "2024-01-15",
            "match_reason": "tag_match",
            "score": 100,
            "matched_resources": [{"address": "aws_s3_bucket.data", "action": "create"}],
        }]
        result = _make_result_with_warnings(warnings)
        output = _format_human(result, _make_changed(), verbose=False)
        assert "[score:" not in output
        assert "Tags:" not in output

    def test_matched_resources_shows_first_only(self):
        # Grouped output shows only the first matched_resource in "Applies to"
        from fixdoc.commands.analyze import _format_human
        matched = [{"address": f"aws_s3_bucket.b{i}", "action": "create"} for i in range(12)]
        warnings = [{
            "id": "abcdef1234567890",
            "short_id": "abcdef12",
            "issue": "S3 ACL error",
            "resolution": "Fixed",
            "tags": "",
            "created_at": "2024-01-15",
            "match_reason": "tag_match",
            "score": 100,
            "matched_resources": matched,
        }]
        result = _make_result_with_warnings(warnings)
        output = _format_human(result, _make_changed())
        assert "Applies to: aws_s3_bucket.b0 (create)" in output


# ===================================================================
# TestAnalyzeFormatJson
# ===================================================================


class TestAnalyzeFormatJson:
    """Tests for resource_warnings in JSON output."""

    def test_json_contains_resource_warnings_key(self):
        from fixdoc.commands.analyze import _format_json
        result = _make_result_with_warnings([])
        data = json.loads(_format_json(result))
        assert "resource_warnings" in data

    def test_json_resource_warnings_entry_fields(self):
        from fixdoc.commands.analyze import _format_json
        warnings = [{
            "id": "abcdef1234567890abcd",
            "short_id": "abcdef12",
            "issue": "S3 error",
            "resolution": "Fixed",
            "tags": "aws_s3_bucket",
            "created_at": "2024-01-15",
            "match_reason": "tag_match",
            "score": 100,
            "matched_resources": [{"address": "aws_s3_bucket.data", "action": "create"}],
        }]
        result = _make_result_with_warnings(warnings)
        data = json.loads(_format_json(result))
        assert len(data["resource_warnings"]) == 1
        entry = data["resource_warnings"][0]
        assert "id" in entry
        assert "short_id" in entry
        assert "issue" in entry
        assert "resolution" in entry
        assert "match_reason" in entry
        assert "score" in entry
        assert "matched_resources" in entry
        assert "created_at" in entry
        # id should be full (not truncated)
        assert entry["id"] == "abcdef1234567890abcd"


# ===================================================================
# TestIAMSensitivity
# ===================================================================


class TestIAMSensitivity:
    """Tests for _extract_principals and _compute_iam_sensitivity."""

    def test_extract_principals_single_service_string(self):
        policy = json.dumps({"Statement": [{"Principal": {"Service": "ec2.amazonaws.com"}}]})
        assert _extract_principals(policy) == {"ec2.amazonaws.com"}

    def test_extract_principals_list_service(self):
        policy = json.dumps({"Statement": [{"Principal": {
            "Service": ["ec2.amazonaws.com", "lambda.amazonaws.com"]
        }}]})
        assert _extract_principals(policy) == {"ec2.amazonaws.com", "lambda.amazonaws.com"}

    def test_extract_principals_wildcard_string(self):
        policy = json.dumps({"Statement": [{"Principal": "*"}]})
        assert "*" in _extract_principals(policy)

    def test_extract_principals_cross_account(self):
        policy = json.dumps({"Statement": [{"Principal": {"AWS": "arn:aws:iam::123456789012:root"}}]})
        assert "arn:aws:iam::123456789012:root" in _extract_principals(policy)

    def test_iam_sensitivity_service_principal_added(self):
        """before: ec2 only; after: ec2 + lambda → +8 field +10 service = 18."""
        before_policy = json.dumps({"Statement": [{"Effect": "Allow",
            "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]})
        after_policy = json.dumps({"Statement": [{"Effect": "Allow",
            "Principal": {"Service": ["ec2.amazonaws.com", "lambda.amazonaws.com"]},
            "Action": "sts:AssumeRole"}]})
        cb = {"before": {"assume_role_policy": before_policy},
              "after":  {"assume_role_policy": after_policy}}
        delta, reason, wildcard = _compute_iam_sensitivity(cb)
        assert delta == 18.0   # +8 sensitive field + +10 service principal
        assert wildcard is False
        assert "lambda.amazonaws.com" in reason

    def test_iam_sensitivity_tag_change_only(self):
        """Only tags changed → delta=0, no wildcard."""
        cb = {"before": {"tags": {"env": "prod"}, "assume_role_policy": "{}"},
              "after":  {"tags": {"env": "staging"}, "assume_role_policy": "{}"}}
        delta, _, wildcard = _compute_iam_sensitivity(cb)
        assert delta == 0.0
        assert wildcard is False

    def test_wildcard_trust_forces_high_floor(self):
        """BlastNode with wildcard_trust=True forces score >= 50 (HIGH)."""
        nodes = [BlastNode("aws_iam_role.r", "aws_iam_role", "update",
                           sensitivity_delta=0, wildcard_trust=True)]
        score = compute_blast_score(nodes, 0, 0, 0)
        assert score >= 50.0
        assert severity_label(score) == "high"


# ===================================================================
# TestBuildScoreExplanation
# ===================================================================


class TestBuildScoreExplanation:
    """Tests for build_score_explanation()."""

    def test_empty_nodes_returns_no_bullets(self):
        result = build_score_explanation([], 0, 0, 0)
        assert result == []

    def test_delete_action_bullet(self):
        nodes = [BlastNode("aws_vpc.main", "aws_vpc", "delete")]
        bullets = build_score_explanation(nodes, 0, 0, 0)
        action_bullets = [b for b in bullets if b.kind == "action"]
        assert len(action_bullets) == 1
        assert "delete" in action_bullets[0].label
        assert action_bullets[0].delta > 0

    def test_iam_sensitivity_bullet(self):
        node = BlastNode(
            "aws_iam_role.api", "aws_iam_role", "update",
            sensitivity_delta=18.0, sensitivity_reason="lambda.amazonaws.com",
        )
        bullets = build_score_explanation([node], 0, 0, 0)
        iam_bullets = [b for b in bullets if b.kind == "iam"]
        assert len(iam_bullets) == 1
        assert iam_bullets[0].delta == 18.0
        assert "aws_iam_role.api" in iam_bullets[0].label

    def test_wildcard_trust_modifier_bullet(self):
        node = BlastNode(
            "aws_iam_role.api", "aws_iam_role", "update",
            sensitivity_delta=8.0, wildcard_trust=True,
        )
        bullets = build_score_explanation([node], 0, 0, 0)
        modifier_bullets = [b for b in bullets if b.kind == "modifier"]
        assert len(modifier_bullets) == 1
        assert "wildcard" in modifier_bullets[0].label.lower()
        assert modifier_bullets[0].delta == 0.0

    def test_l1_impact_bullet(self):
        nodes = [BlastNode("aws_vpc.main", "aws_vpc", "delete")]
        bullets = build_score_explanation(nodes, l1_count=3, l2_count=0, history_count=0)
        impact_bullets = [b for b in bullets if b.kind == "impact"]
        assert len(impact_bullets) == 1
        assert impact_bullets[0].delta == pytest.approx(4.5, abs=0.1)
        assert "3" in impact_bullets[0].label

    def test_history_bullet_single(self):
        nodes = [BlastNode("aws_vpc.main", "aws_vpc", "delete")]
        bullets = build_score_explanation(nodes, 0, 0, history_count=1)
        history_bullets = [b for b in bullets if b.kind == "history"]
        assert len(history_bullets) == 1
        assert history_bullets[0].delta == 5.0

    def test_history_bullet_multiple(self):
        nodes = [BlastNode("aws_vpc.main", "aws_vpc", "delete")]
        bullets = build_score_explanation(nodes, 0, 0, history_count=3)
        history_bullets = [b for b in bullets if b.kind == "history"]
        assert len(history_bullets) == 1
        assert history_bullets[0].delta == 15.0

    def test_greenfield_cap_modifier(self):
        nodes = [
            BlastNode("aws_s3_bucket.a", "aws_s3_bucket", "create"),
            BlastNode("aws_s3_bucket.b", "aws_s3_bucket", "create"),
        ]
        bullets = build_score_explanation(nodes, l1_count=0, l2_count=0, history_count=0)
        modifier_bullets = [b for b in bullets if b.kind == "modifier"]
        assert len(modifier_bullets) == 1
        assert "greenfield" in modifier_bullets[0].label.lower()
        # No greenfield cap when there ARE downstream resources
        bullets_with_impact = build_score_explanation(nodes, l1_count=2, l2_count=0, history_count=0)
        cap_modifiers = [b for b in bullets_with_impact if b.kind == "modifier"]
        assert len(cap_modifiers) == 0

    def test_kinds_are_correct(self):
        node = BlastNode(
            "aws_iam_role.api", "aws_iam_role", "delete",
            sensitivity_delta=8.0, wildcard_trust=True,
        )
        bullets = build_score_explanation([node], l1_count=2, l2_count=1, history_count=2)
        kinds = {b.kind for b in bullets}
        assert "action" in kinds
        assert "iam" in kinds
        assert "modifier" in kinds
        assert "impact" in kinds
        assert "history" in kinds
        for b in bullets:
            assert b.kind in ("action", "iam", "modifier", "impact", "history")

    def test_score_explanation_in_blast_result(self, tmp_path):
        repo = FixRepository(tmp_path)
        plan = make_plan([
            make_resource_change("aws_iam_role.api", "aws_iam_role", ["update"]),
            make_resource_change("aws_s3_bucket.data", "aws_s3_bucket", ["create"]),
        ])
        result = analyze_blast_radius(plan, repo)
        assert isinstance(result.score_explanation, list)
        assert len(result.score_explanation) > 0
        first = result.score_explanation[0]
        assert "label" in first
        assert "delta" in first
        assert "kind" in first
