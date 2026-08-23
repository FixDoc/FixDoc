"""Tests for fixdoc terraform analyzer."""

import json
import pytest
from pathlib import Path

from fixdoc.models import Fix
from fixdoc.storage import FixRepository
from fixdoc.commands.analyze import TerraformAnalyzer, AnalysisMatch


@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary repository for testing."""
    return FixRepository(base_path=tmp_path / ".fixdoc")


@pytest.fixture
def sample_plan(tmp_path):
    """Create a sample terraform plan JSON with actual changes."""
    plan = {
        "resource_changes": [
            {
                "address": "azurerm_storage_account.main",
                "type": "azurerm_storage_account",
                "change": {"actions": ["create"]},
            },
            {
                "address": "azurerm_key_vault.main",
                "type": "azurerm_key_vault",
                "change": {"actions": ["create"]},
            },
        ]
    }

    plan_path = tmp_path / "plan.json"
    with open(plan_path, "w") as f:
        json.dump(plan, f)

    return plan_path


@pytest.fixture
def mixed_plan(tmp_path):
    """Create a plan with both changes and no-ops."""
    plan = {
        "resource_changes": [
            {
                "address": "azurerm_storage_account.main",
                "type": "azurerm_storage_account",
                "change": {"actions": ["create"]},
            },
            {
                "address": "azurerm_key_vault.main",
                "type": "azurerm_key_vault",
                "change": {"actions": ["no-op"]},
            },
            {
                "address": "azurerm_resource_group.main",
                "type": "azurerm_resource_group",
                "change": {"actions": ["no-op"]},
            },
        ]
    }

    plan_path = tmp_path / "mixed_plan.json"
    with open(plan_path, "w") as f:
        json.dump(plan, f)

    return plan_path


class TestTerraformAnalyzer:
    def test_load_plan(self, sample_plan, temp_repo):
        analyzer = TerraformAnalyzer(repo=temp_repo)
        plan = analyzer.load_plan(sample_plan)

        assert "resource_changes" in plan
        assert len(plan["resource_changes"]) == 2

    def test_extract_resource_types(self, sample_plan, temp_repo):
        analyzer = TerraformAnalyzer(repo=temp_repo)
        plan = analyzer.load_plan(sample_plan)
        resources = analyzer.extract_resource_types(plan)

        assert len(resources) == 2
        assert ("azurerm_storage_account.main", "azurerm_storage_account") in resources
        assert ("azurerm_key_vault.main", "azurerm_key_vault") in resources

    def test_get_changed_resources_filters_no_ops(self, mixed_plan, temp_repo):
        """get_changed_resources should only return resources with actual changes."""
        analyzer = TerraformAnalyzer(repo=temp_repo)
        plan = analyzer.load_plan(mixed_plan)
        changed = analyzer.get_changed_resources(plan)

        assert len(changed) == 1
        assert changed[0].address == "azurerm_storage_account.main"
        assert changed[0].action == "create"

    def test_analyze_no_matches(self, sample_plan, temp_repo):
        analyzer = TerraformAnalyzer(repo=temp_repo)
        matches = analyzer.analyze(sample_plan)

        assert len(matches) == 0

    def test_analyze_with_matches(self, sample_plan, temp_repo):
        fix = Fix(
            issue="Users couldn't access storage",
            resolution="Added blob contributor role",
            tags="azurerm_storage_account,rbac",
        )
        temp_repo.save(fix)

        analyzer = TerraformAnalyzer(repo=temp_repo)
        matches = analyzer.analyze(sample_plan)

        assert len(matches) == 1
        assert matches[0].resource_type == "azurerm_storage_account"
        assert matches[0].related_fix.id == fix.id

    def test_analyze_multiple_matches(self, sample_plan, temp_repo):
        fix1 = Fix(issue="Storage issue", resolution="Fixed", tags="azurerm_storage_account")
        fix2 = Fix(issue="Key vault issue", resolution="Fixed", tags="azurerm_key_vault")
        temp_repo.save(fix1)
        temp_repo.save(fix2)

        analyzer = TerraformAnalyzer(repo=temp_repo)
        matches = analyzer.analyze(sample_plan)

        assert len(matches) == 2

    def test_analyze_skips_no_op_resources(self, mixed_plan, temp_repo):
        """Analyze should not match against no-op resources."""
        fix = Fix(issue="Key vault issue", resolution="Fixed", tags="azurerm_key_vault")
        temp_repo.save(fix)

        analyzer = TerraformAnalyzer(repo=temp_repo)
        matches = analyzer.analyze(mixed_plan)

        # key_vault is no-op, so should not match
        assert len(matches) == 0

    def test_analyze_and_format_no_matches(self, sample_plan, temp_repo):
        analyzer = TerraformAnalyzer(repo=temp_repo)
        output = analyzer.analyze_and_format(sample_plan)

        assert "No known issues found" in output

    def test_analyze_and_format_with_matches(self, sample_plan, temp_repo):
        fix = Fix(
            issue="Storage access denied",
            resolution="Added contributor role",
            tags="azurerm_storage_account",
        )
        temp_repo.save(fix)

        analyzer = TerraformAnalyzer(repo=temp_repo)
        output = analyzer.analyze_and_format(sample_plan)

        assert "FIX-" in output
        assert "Storage access denied" in output
        assert "Added contributor role" in output

    def test_replace_action_detected(self, tmp_path, temp_repo):
        """create+delete actions should be detected as replace."""
        plan = {
            "resource_changes": [
                {
                    "address": "aws_s3_bucket.data",
                    "type": "aws_s3_bucket",
                    "change": {"actions": ["create", "delete"]},
                }
            ]
        }
        plan_path = tmp_path / "replace_plan.json"
        with open(plan_path, "w") as f:
            json.dump(plan, f)

        analyzer = TerraformAnalyzer(repo=temp_repo)
        plan_data = analyzer.load_plan(plan_path)
        changed = analyzer.get_changed_resources(plan_data)

        assert len(changed) == 1
        assert changed[0].action == "replace"


class TestAnalysisMatch:
    def test_format_warning(self, temp_repo):
        fix = Fix(
            issue="Users couldn't access blob storage",
            resolution="Added storage blob data contributor role",
            tags="azurerm_storage_account,rbac",
        )
        temp_repo.save(fix)

        match = AnalysisMatch(
            resource_address="azurerm_storage_account.main",
            resource_type="azurerm_storage_account",
            related_fix=fix,
        )

        warning = match.format_warning()

        assert "FIX-" in warning
        assert "Resolution:" in warning
        assert "Tags:" in warning
