"""Tests for fixdoc models."""

import pytest
from fixdoc.models import Fix


class TestFix:
    def test_create_fix_required_fields(self):
        fix = Fix(
            issue="Storage account access denied", resolution="Added storage blob contributor role"
        )

        assert fix.issue == "Storage account access denied"
        assert fix.resolution == "Added storage blob contributor role"
        assert fix.id is not None
        assert fix.created_at is not None
        assert fix.updated_at is not None

    def test_create_fix_all_fields(self):
        fix = Fix(
            issue="Storage account access denied",
            resolution="Added storage blob contributor role",
            error_excerpt="AuthorizationFailed: User does not have access",
            tags="azurerm_storage_account,rbac",
            notes="Make sure to wait 5 minutes for RBAC to propagate",
        )

        assert fix.error_excerpt == "AuthorizationFailed: User does not have access"
        assert fix.tags == "azurerm_storage_account,rbac"
        assert fix.notes == "Make sure to wait 5 minutes for RBAC to propagate"

    def test_to_dict(self):
        fix = Fix(issue="Test issue", resolution="Test resolution")

        d = fix.to_dict()

        assert d["issue"] == "Test issue"
        assert d["resolution"] == "Test resolution"
        assert "id" in d
        assert "created_at" in d

    def test_from_dict(self):
        data = {
            "id": "test-uuid",
            "issue": "Test issue",
            "resolution": "Test resolution",
            "tags": "test,tags",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
        }

        fix = Fix.from_dict(data)

        assert fix.id == "test-uuid"
        assert fix.issue == "Test issue"
        assert fix.tags == "test,tags"

    def test_to_markdown(self):
        from fixdoc.formatter import fix_to_markdown

        fix = Fix(
            issue="Storage account access denied",
            resolution="Added storage blob contributor role",
            error_excerpt="AuthorizationFailed",
            tags="storage,rbac",
        )

        md = fix_to_markdown(fix)

        assert "## Issue" in md
        assert "Storage account access denied" in md
        assert "## Resolution" in md
        assert "## Error Excerpt" in md
        assert "```" in md
        assert "**Tags:** `storage,rbac`" in md

    def test_summary(self):
        fix = Fix(
            issue="This is a very long issue description that should be truncated in the summary display",
            resolution="Test resolution",
            tags="test",
        )

        summary = fix.summary()

        assert fix.id[:8] in summary
        assert "[test]" in summary
        assert "..." in summary  # Should be truncated

    def test_matches_case_insensitive(self):
        fix = Fix(issue="Storage ACCOUNT access denied", resolution="Added role")

        assert fix.matches("storage")
        assert fix.matches("STORAGE")
        assert fix.matches("Storage")
        assert fix.matches("account")
        assert not fix.matches("kubernetes")

    def test_matches_searches_all_fields(self):
        fix = Fix(
            issue="Issue text",
            resolution="Resolution text",
            error_excerpt="Error text",
            tags="tag1,tag2",
            notes="Notes text",
        )

        assert fix.matches("issue")
        assert fix.matches("resolution")
        assert fix.matches("error")
        assert fix.matches("tag1")
        assert fix.matches("notes")

    def test_matches_resource_type(self):
        fix = Fix(issue="Test", resolution="Test", tags="azurerm_storage_account,rbac")

        assert fix.matches_resource_type("azurerm_storage_account")
        assert fix.matches_resource_type("AZURERM_STORAGE_ACCOUNT")
        assert not fix.matches_resource_type("azurerm_key_vault")

    def test_matches_resource_type_no_tags(self):
        fix = Fix(issue="Test", resolution="Test")

        assert not fix.matches_resource_type("anything")


# ===================================================================
# TestSourceErrorIds — Feature 2
# ===================================================================


class TestSourceErrorIds:
    """Tests for source_error_ids field on Fix."""

    def test_source_error_ids_default_none(self):
        fix = Fix(issue="test", resolution="test")
        assert fix.source_error_ids is None

    def test_source_error_ids_to_dict_roundtrip(self):
        fix = Fix(
            issue="test",
            resolution="test",
            source_error_ids=["abc123", "def456"],
        )
        d = fix.to_dict()
        restored = Fix.from_dict(d)
        assert restored.source_error_ids == ["abc123", "def456"]

    def test_from_dict_without_source_error_ids(self):
        """Backward compat: old dicts without source_error_ids still work."""
        d = {"issue": "test", "resolution": "test"}
        fix = Fix.from_dict(d)
        assert fix.source_error_ids is None

    def test_source_error_ids_in_dict(self):
        fix = Fix(
            issue="test",
            resolution="test",
            source_error_ids=["err1"],
        )
        d = fix.to_dict()
        assert d["source_error_ids"] == ["err1"]


# ===================================================================
# TestEffectiveness — Fix Effectiveness Tracking
# ===================================================================


class TestEffectiveness:
    """Tests for effectiveness tracking fields on Fix."""

    def test_effectiveness_fields_default_values(self):
        fix = Fix(issue="test", resolution="test")
        assert fix.applied_count == 0
        assert fix.success_count == 0
        assert fix.last_applied_at is None

    def test_effectiveness_rate_zero_applied(self):
        fix = Fix(issue="test", resolution="test")
        assert fix.effectiveness_rate is None

    def test_effectiveness_rate_all_success(self):
        fix = Fix(issue="test", resolution="test", applied_count=5, success_count=5)
        assert fix.effectiveness_rate == 1.0

    def test_effectiveness_rate_partial(self):
        fix = Fix(issue="test", resolution="test", applied_count=4, success_count=3)
        assert fix.effectiveness_rate == 0.75

    def test_effectiveness_to_dict_roundtrip(self):
        fix = Fix(
            issue="test",
            resolution="test",
            applied_count=3,
            success_count=2,
            last_applied_at="2026-03-16T00:00:00+00:00",
        )
        d = fix.to_dict()
        restored = Fix.from_dict(d)
        assert restored.applied_count == 3
        assert restored.success_count == 2
        assert restored.last_applied_at == "2026-03-16T00:00:00+00:00"

    def test_from_dict_backward_compat(self):
        """Old dicts without effectiveness fields still work."""
        d = {"issue": "test", "resolution": "test"}
        fix = Fix.from_dict(d)
        assert fix.applied_count == 0
        assert fix.success_count == 0
        assert fix.last_applied_at is None
        assert fix.effectiveness_rate is None


# ===================================================================
# TestMemoryType — Forward-compatible memory_type field
# ===================================================================


class TestMemoryType:
    """Tests for memory_type field on Fix."""

    def test_memory_type_defaults_to_fix(self):
        fix = Fix(issue="test", resolution="test")
        assert fix.memory_type == "fix"

    def test_memory_type_roundtrip(self):
        fix = Fix(issue="test", resolution="test", memory_type="check")
        d = fix.to_dict()
        restored = Fix.from_dict(d)
        assert restored.memory_type == "check"

    def test_from_dict_without_memory_type(self):
        """Backward compat: old dicts without memory_type still work."""
        d = {"issue": "test", "resolution": "test"}
        fix = Fix.from_dict(d)
        assert fix.memory_type == "fix"

    def test_custom_memory_type_preserved(self):
        fix = Fix(issue="test", resolution="test", memory_type="playbook")
        assert fix.memory_type == "playbook"
        assert fix.to_dict()["memory_type"] == "playbook"
