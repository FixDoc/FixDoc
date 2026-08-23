"""Tests for edit command."""

import pytest

from fixdoc.models import Fix
from fixdoc.storage import FixRepository


@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary repository for testing."""
    return FixRepository(base_path=tmp_path / ".fixdoc")


@pytest.fixture
def sample_fix(temp_repo):
    """Create a sample fix for testing."""
    fix = Fix(
        issue="Original issue",
        resolution="Original resolution",
        tags="original,tags",
        notes="Original notes",
        error_excerpt="Original error",
    )
    return temp_repo.save(fix)


class TestFixTouch:
    def test_touch_updates_timestamp(self, sample_fix):
        original_updated = sample_fix.updated_at

        sample_fix.touch()

        assert sample_fix.updated_at != original_updated

    def test_touch_preserves_created_at(self, sample_fix):
        original_created = sample_fix.created_at

        sample_fix.touch()

        assert sample_fix.created_at == original_created


class TestEditFix:
    def test_edit_issue(self, temp_repo, sample_fix):
        sample_fix.issue = "Updated issue"
        sample_fix.touch()
        temp_repo.save(sample_fix)

        retrieved = temp_repo.get(sample_fix.id)

        assert retrieved.issue == "Updated issue"
        assert retrieved.resolution == "Original resolution"

    def test_edit_resolution(self, temp_repo, sample_fix):
        sample_fix.resolution = "Updated resolution"
        sample_fix.touch()
        temp_repo.save(sample_fix)

        retrieved = temp_repo.get(sample_fix.id)

        assert retrieved.resolution == "Updated resolution"
        assert retrieved.issue == "Original issue"

    def test_edit_tags(self, temp_repo, sample_fix):
        sample_fix.tags = "new,updated,tags"
        sample_fix.touch()
        temp_repo.save(sample_fix)

        retrieved = temp_repo.get(sample_fix.id)

        assert retrieved.tags == "new,updated,tags"

    def test_edit_notes(self, temp_repo, sample_fix):
        sample_fix.notes = "Updated notes"
        sample_fix.touch()
        temp_repo.save(sample_fix)

        retrieved = temp_repo.get(sample_fix.id)

        assert retrieved.notes == "Updated notes"

    def test_edit_error_excerpt(self, temp_repo, sample_fix):
        sample_fix.error_excerpt = "Updated error"
        sample_fix.touch()
        temp_repo.save(sample_fix)

        retrieved = temp_repo.get(sample_fix.id)

        assert retrieved.error_excerpt == "Updated error"

    def test_edit_multiple_fields(self, temp_repo, sample_fix):
        sample_fix.issue = "New issue"
        sample_fix.resolution = "New resolution"
        sample_fix.tags = "new,tags"
        sample_fix.touch()
        temp_repo.save(sample_fix)

        retrieved = temp_repo.get(sample_fix.id)

        assert retrieved.issue == "New issue"
        assert retrieved.resolution == "New resolution"
        assert retrieved.tags == "new,tags"

    def test_edit_clears_optional_field(self, temp_repo, sample_fix):
        sample_fix.notes = None
        sample_fix.touch()
        temp_repo.save(sample_fix)

        retrieved = temp_repo.get(sample_fix.id)

        assert retrieved.notes is None

    def test_edit_preserves_id(self, temp_repo, sample_fix):
        original_id = sample_fix.id

        sample_fix.issue = "Updated issue"
        sample_fix.touch()
        temp_repo.save(sample_fix)

        retrieved = temp_repo.get(original_id)

        assert retrieved.id == original_id

    def test_edit_updates_markdown(self, temp_repo, sample_fix):
        sample_fix.resolution = "Updated resolution"
        sample_fix.touch()
        temp_repo.save(sample_fix)

        md_path = temp_repo.docs_path / f"{sample_fix.id}.md"
        content = md_path.read_text()

        assert "Updated resolution" in content

    def test_edit_nonexistent_fix(self, temp_repo):
        result = temp_repo.get("nonexistent")

        assert result is None

    def test_edit_with_partial_id(self, temp_repo, sample_fix):
        short_id = sample_fix.id[:8]

        retrieved = temp_repo.get(short_id)
        retrieved.issue = "Updated via partial ID"
        retrieved.touch()
        temp_repo.save(retrieved)

        final = temp_repo.get(short_id)

        assert final.issue == "Updated via partial ID"
