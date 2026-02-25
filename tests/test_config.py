"""Tests for fixdoc configuration management."""

import os
import pytest
from pathlib import Path

from fixdoc.config import (
    ConfigManager,
    FixDocConfig,
    SyncConfig,
    UserConfig,
    DisplayConfig,
    CaptureConfig,
    SuggestionWeights,
    resolve_base_path,
)


class TestFixDocConfig:
    def test_default_config(self):
        config = FixDocConfig()

        assert config.sync.remote_url is None
        assert config.sync.branch == "main"
        assert config.sync.auto_pull is False
        assert config.user.name is None
        assert config.user.email is None
        assert config.private_fixes == []

    def test_default_display_config(self):
        config = FixDocConfig()

        assert config.display.search_result_limit == 10
        assert config.display.list_result_limit == 20
        assert config.display.top_tags_limit == 10

    def test_default_capture_config(self):
        config = FixDocConfig()

        assert config.capture.error_excerpt_max_chars == 2000
        assert config.capture.max_suggestions_shown == 3
        assert config.capture.similar_fix_limit == 5

    def test_default_suggestion_weights(self):
        config = FixDocConfig()

        assert config.suggestion_weights.tag_weight == 5
        assert config.suggestion_weights.error_code_weight == 20
        assert config.suggestion_weights.issue_keyword_weight == 2
        assert config.suggestion_weights.resolution_keyword_weight == 1
        assert config.suggestion_weights.resource_type_weight == 8

    def test_to_dict(self):
        config = FixDocConfig(
            sync=SyncConfig(
                remote_url="git@github.com:test/repo.git",
                branch="develop",
                auto_pull=True,
            ),
            user=UserConfig(name="John Doe", email="john@example.com"),
            private_fixes=["fix-1", "fix-2"],
        )

        d = config.to_dict()

        assert d["sync"]["remote_url"] == "git@github.com:test/repo.git"
        assert d["sync"]["branch"] == "develop"
        assert d["sync"]["auto_pull"] is True
        assert d["user"]["name"] == "John Doe"
        assert d["user"]["email"] == "john@example.com"
        assert d["private_fixes"] == ["fix-1", "fix-2"]

    def test_to_dict_includes_new_sections(self):
        config = FixDocConfig(
            display=DisplayConfig(search_result_limit=5),
            capture=CaptureConfig(error_excerpt_max_chars=3000),
            suggestion_weights=SuggestionWeights(tag_weight=20),
        )

        d = config.to_dict()

        assert d["display"]["search_result_limit"] == 5
        assert d["display"]["list_result_limit"] == 20
        assert d["capture"]["error_excerpt_max_chars"] == 3000
        assert d["capture"]["max_suggestions_shown"] == 3
        assert d["suggestion_weights"]["tag_weight"] == 20
        assert d["suggestion_weights"]["error_code_weight"] == 20

    def test_from_dict(self):
        data = {
            "sync": {
                "remote_url": "https://github.com/test/repo.git",
                "branch": "main",
                "auto_pull": False,
            },
            "user": {"name": "Jane Doe", "email": "jane@example.com"},
            "private_fixes": ["abc123"],
        }

        config = FixDocConfig.from_dict(data)

        assert config.sync.remote_url == "https://github.com/test/repo.git"
        assert config.sync.branch == "main"
        assert config.user.name == "Jane Doe"
        assert config.private_fixes == ["abc123"]

    def test_from_dict_with_defaults(self):
        """Empty dict produces correct defaults for all fields (backward compat)."""
        data = {}

        config = FixDocConfig.from_dict(data)

        assert config.sync.remote_url is None
        assert config.sync.branch == "main"
        assert config.user.name is None
        assert config.private_fixes == []
        # New fields should also get defaults
        assert config.display.search_result_limit == 10
        assert config.display.list_result_limit == 20
        assert config.display.top_tags_limit == 10
        assert config.capture.error_excerpt_max_chars == 2000
        assert config.capture.max_suggestions_shown == 3
        assert config.capture.similar_fix_limit == 5
        assert config.suggestion_weights.tag_weight == 5
        assert config.suggestion_weights.error_code_weight == 20
        assert config.suggestion_weights.issue_keyword_weight == 2
        assert config.suggestion_weights.resolution_keyword_weight == 1
        assert config.suggestion_weights.resource_type_weight == 8

    def test_from_dict_partial_config(self):
        """Only display section present, others get defaults."""
        data = {
            "display": {"search_result_limit": 5},
        }

        config = FixDocConfig.from_dict(data)

        assert config.display.search_result_limit == 5
        assert config.display.list_result_limit == 20
        assert config.display.top_tags_limit == 10
        assert config.capture.error_excerpt_max_chars == 2000
        assert config.suggestion_weights.tag_weight == 5
        assert config.sync.remote_url is None

    def test_from_dict_with_new_sections(self):
        data = {
            "display": {
                "search_result_limit": 25,
                "list_result_limit": 50,
                "top_tags_limit": 15,
            },
            "capture": {
                "error_excerpt_max_chars": 5000,
                "max_suggestions_shown": 5,
                "similar_fix_limit": 10,
            },
            "suggestion_weights": {
                "tag_weight": 20,
                "error_code_weight": 25,
                "issue_keyword_weight": 5,
                "resolution_keyword_weight": 4,
                "resource_type_weight": 12,
            },
        }

        config = FixDocConfig.from_dict(data)

        assert config.display.search_result_limit == 25
        assert config.display.list_result_limit == 50
        assert config.display.top_tags_limit == 15
        assert config.capture.error_excerpt_max_chars == 5000
        assert config.capture.max_suggestions_shown == 5
        assert config.capture.similar_fix_limit == 10
        assert config.suggestion_weights.tag_weight == 20
        assert config.suggestion_weights.error_code_weight == 25
        assert config.suggestion_weights.issue_keyword_weight == 5
        assert config.suggestion_weights.resolution_keyword_weight == 4
        assert config.suggestion_weights.resource_type_weight == 12


class TestConfigManager:
    def test_load_nonexistent_returns_default(self, tmp_path):
        manager = ConfigManager(tmp_path)

        config = manager.load()

        assert config.sync.remote_url is None
        assert config.user.name is None

    def test_save_and_load(self, tmp_path):
        manager = ConfigManager(tmp_path)
        config = FixDocConfig(
            sync=SyncConfig(remote_url="git@github.com:test/repo.git"),
            user=UserConfig(name="Test User", email="test@example.com"),
        )

        manager.save(config)
        loaded = manager.load()

        assert loaded.sync.remote_url == "git@github.com:test/repo.git"
        assert loaded.user.name == "Test User"
        assert loaded.user.email == "test@example.com"

    def test_save_and_load_with_new_sections(self, tmp_path):
        """Round-trip: save config with custom values, load it back, verify."""
        manager = ConfigManager(tmp_path)
        config = FixDocConfig(
            display=DisplayConfig(search_result_limit=5, list_result_limit=50),
            capture=CaptureConfig(error_excerpt_max_chars=3000, max_suggestions_shown=7),
            suggestion_weights=SuggestionWeights(tag_weight=20, error_code_weight=30),
        )

        manager.save(config)
        loaded = manager.load()

        assert loaded.display.search_result_limit == 5
        assert loaded.display.list_result_limit == 50
        assert loaded.display.top_tags_limit == 10  # default
        assert loaded.capture.error_excerpt_max_chars == 3000
        assert loaded.capture.max_suggestions_shown == 7
        assert loaded.capture.similar_fix_limit == 5  # default
        assert loaded.suggestion_weights.tag_weight == 20
        assert loaded.suggestion_weights.error_code_weight == 30
        assert loaded.suggestion_weights.issue_keyword_weight == 2  # default

    def test_is_sync_configured(self, tmp_path):
        manager = ConfigManager(tmp_path)

        assert manager.is_sync_configured() is False

        config = FixDocConfig(
            sync=SyncConfig(remote_url="git@github.com:test/repo.git")
        )
        manager.save(config)

        assert manager.is_sync_configured() is True

    def test_add_private_fix(self, tmp_path):
        manager = ConfigManager(tmp_path)

        manager.add_private_fix("fix-123")
        manager.add_private_fix("fix-456")
        manager.add_private_fix("fix-123")  # Duplicate

        config = manager.load()
        assert "fix-123" in config.private_fixes
        assert "fix-456" in config.private_fixes
        assert len(config.private_fixes) == 2  # No duplicate

    def test_remove_private_fix(self, tmp_path):
        manager = ConfigManager(tmp_path)
        manager.add_private_fix("fix-123")
        manager.add_private_fix("fix-456")

        manager.remove_private_fix("fix-123")

        config = manager.load()
        assert "fix-123" not in config.private_fixes
        assert "fix-456" in config.private_fixes

    def test_is_fix_private(self, tmp_path):
        manager = ConfigManager(tmp_path)
        manager.add_private_fix("fix-123")

        assert manager.is_fix_private("fix-123") is True
        assert manager.is_fix_private("fix-456") is False

    def test_creates_directory_on_save(self, tmp_path):
        nested_path = tmp_path / "nested" / "path"
        manager = ConfigManager(nested_path)
        config = FixDocConfig()

        manager.save(config)

        assert nested_path.exists()
        assert (nested_path / "config.yaml").exists()


class TestResolveBasePath:
    def test_default_path(self, monkeypatch):
        monkeypatch.delenv("FIXDOC_HOME", raising=False)

        path = resolve_base_path()

        assert path == Path.home() / ".fixdoc"

    def test_env_var_override(self, monkeypatch, tmp_path):
        custom_path = str(tmp_path / "custom_fixdoc")
        monkeypatch.setenv("FIXDOC_HOME", custom_path)

        path = resolve_base_path()

        assert path == Path(custom_path)

    def test_env_var_empty_string_uses_default(self, monkeypatch):
        monkeypatch.setenv("FIXDOC_HOME", "")

        path = resolve_base_path()

        # Empty string is falsy, should fall back to default
        assert path == Path.home() / ".fixdoc"
