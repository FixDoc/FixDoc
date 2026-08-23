"""Tests for markdown parsing (reverse of formatter)."""

import pytest

from fixdoc.models import Fix
from fixdoc.formatter import fix_to_markdown
from fixdoc.markdown_parser import markdown_to_fix, MarkdownParseError


class TestMarkdownParser:
    def test_round_trip_basic(self):
        """Test that Fix -> markdown -> Fix preserves data."""
        original = Fix(
            id="test-uuid-1234",
            issue="Storage account access denied",
            resolution="Added storage blob contributor role",
            created_at="2024-01-15T10:30:00+00:00",
            updated_at="2024-01-15T10:30:00+00:00",
        )

        markdown = fix_to_markdown(original)
        parsed = markdown_to_fix(markdown, original.id)

        assert parsed.issue == original.issue
        assert parsed.resolution == original.resolution
        assert parsed.created_at == original.created_at
        assert parsed.updated_at == original.updated_at

    def test_round_trip_with_all_fields(self):
        """Test round trip with all optional fields."""
        original = Fix(
            id="test-uuid-5678",
            issue="Key vault access policy missing",
            resolution="Added get and list secrets permissions",
            error_excerpt="AccessDenied: User is not authorized",
            tags="azurerm_key_vault,rbac",
            notes="Remember to check AAD permissions too",
            created_at="2024-01-15T10:30:00+00:00",
            updated_at="2024-01-16T14:00:00+00:00",
            author="John Doe",
            author_email="john@example.com",
        )

        markdown = fix_to_markdown(original)
        parsed = markdown_to_fix(markdown, original.id)

        assert parsed.issue == original.issue
        assert parsed.resolution == original.resolution
        assert parsed.error_excerpt == original.error_excerpt
        assert parsed.tags == original.tags
        assert parsed.notes == original.notes
        assert parsed.author == original.author
        assert parsed.author_email == original.author_email

    def test_parse_missing_issue_raises_error(self):
        """Test that missing Issue section raises error."""
        markdown = """# Fix: abc12345

**Created:** 2024-01-15T10:30:00+00:00
**Updated:** 2024-01-15T10:30:00+00:00

## Resolution

Some resolution text
"""
        with pytest.raises(MarkdownParseError, match="Missing required 'Issue' section"):
            markdown_to_fix(markdown, "test-id")

    def test_parse_missing_resolution_raises_error(self):
        """Test that missing Resolution section raises error."""
        markdown = """# Fix: abc12345

**Created:** 2024-01-15T10:30:00+00:00
**Updated:** 2024-01-15T10:30:00+00:00

## Issue

Some issue text
"""
        with pytest.raises(MarkdownParseError, match="Missing required 'Resolution' section"):
            markdown_to_fix(markdown, "test-id")

    def test_parse_multiline_issue(self):
        """Test parsing multiline issue text."""
        markdown = """# Fix: abc12345

**Created:** 2024-01-15T10:30:00+00:00
**Updated:** 2024-01-15T10:30:00+00:00

## Issue

This is a multiline issue.
It spans multiple lines.
And has different paragraphs.

## Resolution

Fixed it.
"""
        parsed = markdown_to_fix(markdown, "test-id")

        assert "multiline issue" in parsed.issue
        assert "multiple lines" in parsed.issue

    def test_parse_code_block_in_error_excerpt(self):
        """Test parsing code block in Error Excerpt section."""
        markdown = """# Fix: abc12345

**Created:** 2024-01-15T10:30:00+00:00
**Updated:** 2024-01-15T10:30:00+00:00

## Issue

Some issue

## Resolution

Some resolution

## Error Excerpt

```
Error: AuthorizationFailed
  User does not have permission
  Code: 403
```
"""
        parsed = markdown_to_fix(markdown, "test-id")

        assert "AuthorizationFailed" in parsed.error_excerpt
        assert "403" in parsed.error_excerpt

    def test_parse_tags_with_backticks(self):
        """Test parsing tags in backtick format."""
        markdown = """# Fix: abc12345

**Created:** 2024-01-15T10:30:00+00:00
**Updated:** 2024-01-15T10:30:00+00:00

**Tags:** `storage,rbac,azure`

## Issue

Some issue

## Resolution

Some resolution
"""
        parsed = markdown_to_fix(markdown, "test-id")

        assert parsed.tags == "storage,rbac,azure"

    def test_parse_without_optional_sections(self):
        """Test parsing markdown without optional sections."""
        markdown = """# Fix: abc12345

**Created:** 2024-01-15T10:30:00+00:00
**Updated:** 2024-01-15T10:30:00+00:00

## Issue

Minimal issue description

## Resolution

Minimal resolution
"""
        parsed = markdown_to_fix(markdown, "test-id")

        assert parsed.issue == "Minimal issue description"
        assert parsed.resolution == "Minimal resolution"
        assert parsed.error_excerpt is None
        assert parsed.tags is None
        assert parsed.notes is None

    def test_preserves_fix_id(self):
        """Test that the provided fix ID is used."""
        markdown = """# Fix: abc12345

**Created:** 2024-01-15T10:30:00+00:00
**Updated:** 2024-01-15T10:30:00+00:00

## Issue

Test issue

## Resolution

Test resolution
"""
        parsed = markdown_to_fix(markdown, "full-uuid-from-filename")

        assert parsed.id == "full-uuid-from-filename"


# ===================================================================
# TestSourceErrorIdsMarkdown — Feature 2
# ===================================================================


class TestSourceErrorIdsMarkdown:
    """Tests for source_error_ids markdown roundtrip."""

    def test_source_error_ids_markdown_roundtrip(self):
        from fixdoc.formatter import fix_to_markdown

        fix = Fix(
            issue="AccessDenied",
            resolution="Added binding",
            source_error_ids=["abc123def456"],
        )
        md = fix_to_markdown(fix)
        parsed = markdown_to_fix(md, fix.id)
        assert parsed.source_error_ids == ["abc123def456"]

    def test_markdown_without_source_error_ids_section(self):
        """Old markdown without Source Error IDs section still works."""
        markdown = """# Fix: abc12345

**Created:** 2024-01-15T10:30:00+00:00
**Updated:** 2024-01-15T10:30:00+00:00

## Issue

Test issue

## Resolution

Test resolution
"""
        parsed = markdown_to_fix(markdown, "abc12345")
        assert parsed.source_error_ids is None

    def test_multiple_source_error_ids(self):
        from fixdoc.formatter import fix_to_markdown

        fix = Fix(
            issue="Multiple errors",
            resolution="Fixed them all",
            source_error_ids=["id1", "id2", "id3"],
        )
        md = fix_to_markdown(fix)
        parsed = markdown_to_fix(md, fix.id)
        assert parsed.source_error_ids == ["id1", "id2", "id3"]


# ===================================================================
# TestMemoryTypeMarkdown — Phase 2
# ===================================================================


class TestMemoryTypeMarkdown:
    """Tests for memory_type markdown roundtrip."""

    def test_memory_type_roundtrip_non_fix(self):
        """Non-fix memory_type round-trips through markdown."""
        from fixdoc.formatter import fix_to_markdown

        fix = Fix(
            issue="Deployment failed",
            resolution="1. Stop\n2. Update\n3. Restart",
            memory_type="playbook",
        )
        md = fix_to_markdown(fix)
        assert "**Memory Type:** playbook" in md
        parsed = markdown_to_fix(md, fix.id)
        assert parsed.memory_type == "playbook"

    def test_memory_type_missing_defaults_to_fix(self):
        """Old markdown without Memory Type line defaults to 'fix'."""
        markdown = """# Fix: abc12345

**Created:** 2024-01-15T10:30:00+00:00
**Updated:** 2024-01-15T10:30:00+00:00

## Issue

Test issue

## Resolution

Test resolution
"""
        parsed = markdown_to_fix(markdown, "abc12345")
        assert parsed.memory_type == "fix"

    def test_memory_type_fix_not_emitted_in_markdown(self):
        """Fix type (default) should NOT emit Memory Type line."""
        from fixdoc.formatter import fix_to_markdown

        fix = Fix(
            issue="Access denied",
            resolution="Added IAM binding",
            memory_type="fix",
        )
        md = fix_to_markdown(fix)
        assert "**Memory Type:**" not in md

    def test_memory_type_check_roundtrip(self):
        from fixdoc.formatter import fix_to_markdown

        fix = Fix(
            issue="SG misconfigured",
            resolution="Verify the ingress rules",
            memory_type="check",
        )
        md = fix_to_markdown(fix)
        parsed = markdown_to_fix(md, fix.id)
        assert parsed.memory_type == "check"

    def test_memory_type_insight_roundtrip(self):
        from fixdoc.formatter import fix_to_markdown

        fix = Fix(
            issue="Provider drift",
            resolution="Root cause was stale lock file",
            memory_type="insight",
        )
        md = fix_to_markdown(fix)
        parsed = markdown_to_fix(md, fix.id)
        assert parsed.memory_type == "insight"
