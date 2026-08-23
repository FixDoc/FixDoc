"""Tests for fixdoc storage."""

import json
import pytest
from pathlib import Path

from fixdoc.models import Fix
from fixdoc.storage import FixRepository


@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary repository for testing."""
    return FixRepository(base_path=tmp_path / ".fixdoc")


class TestFixRepository:
    def test_init_creates_directories(self, temp_repo):
        assert temp_repo.base_path.exists()
        assert temp_repo.docs_path.exists()
        assert temp_repo.db_path.exists()

    def test_save_and_get(self, temp_repo):
        fix = Fix(issue="Test issue", resolution="Test resolution", tags="test")

        saved = temp_repo.save(fix)
        retrieved = temp_repo.get(saved.id)

        assert retrieved is not None
        assert retrieved.id == saved.id
        assert retrieved.issue == "Test issue"

    def test_save_creates_markdown(self, temp_repo):
        fix = Fix(issue="Test issue", resolution="Test resolution")

        saved = temp_repo.save(fix)
        md_path = temp_repo.docs_path / f"{saved.id}.md"

        assert md_path.exists()
        content = md_path.read_text()
        assert "Test issue" in content

    def test_get_partial_id(self, temp_repo):
        fix = Fix(issue="Test issue", resolution="Test resolution")

        saved = temp_repo.save(fix)
        short_id = saved.id[:8]

        retrieved = temp_repo.get(short_id)

        assert retrieved is not None
        assert retrieved.id == saved.id

    def test_get_not_found(self, temp_repo):
        result = temp_repo.get("nonexistent")
        assert result is None

    def test_list_all(self, temp_repo):
        fix1 = Fix(issue="Issue 1", resolution="Resolution 1")
        fix2 = Fix(issue="Issue 2", resolution="Resolution 2")

        temp_repo.save(fix1)
        temp_repo.save(fix2)

        all_fixes = temp_repo.list_all()

        assert len(all_fixes) == 2

    def test_search(self, temp_repo):
        fix1 = Fix(issue="Storage account error", resolution="Added role")
        fix2 = Fix(issue="Key vault issue", resolution="Updated policy")

        temp_repo.save(fix1)
        temp_repo.save(fix2)

        results = temp_repo.search("storage")

        assert len(results) == 1
        assert results[0].issue == "Storage account error"

    def test_search_case_insensitive(self, temp_repo):
        fix = Fix(issue="STORAGE account", resolution="test")
        temp_repo.save(fix)

        results = temp_repo.search("storage")

        assert len(results) == 1

    def test_find_by_resource_type(self, temp_repo):
        fix1 = Fix(issue="Storage error", resolution="Fixed", tags="azurerm_storage_account,rbac")
        fix2 = Fix(issue="Key vault error", resolution="Fixed", tags="azurerm_key_vault")

        temp_repo.save(fix1)
        temp_repo.save(fix2)

        results = temp_repo.find_by_resource_type("azurerm_storage_account")

        assert len(results) == 1
        assert "storage" in results[0].issue.lower()

    def test_delete(self, temp_repo):
        fix = Fix(issue="Test", resolution="Test")
        saved = temp_repo.save(fix)

        assert temp_repo.count() == 1

        result = temp_repo.delete(saved.id)

        assert result is True
        assert temp_repo.count() == 0
        assert not (temp_repo.docs_path / f"{saved.id}.md").exists()

    def test_delete_not_found(self, temp_repo):
        result = temp_repo.delete("nonexistent")
        assert result is False

    def test_count(self, temp_repo):
        assert temp_repo.count() == 0

        temp_repo.save(Fix(issue="1", resolution="1"))
        assert temp_repo.count() == 1

        temp_repo.save(Fix(issue="2", resolution="2"))
        assert temp_repo.count() == 2

    def test_update_existing_fix(self, temp_repo):
        fix = Fix(issue="Original issue", resolution="Original resolution")
        saved = temp_repo.save(fix)

        # Modify and save again
        saved.issue = "Updated issue"
        temp_repo.save(saved)

        # Should still be only one fix
        assert temp_repo.count() == 1

        retrieved = temp_repo.get(saved.id)
        assert retrieved.issue == "Updated issue"
