"""Tests for fixdoc init — one command from empty repo to agent-ready."""

import importlib
import json

import yaml
from click.testing import CliRunner

from fixdoc.cli import create_cli


def run_init(tmp_path, *args):
    runner = CliRunner()
    return runner.invoke(create_cli(), ["init", "--store", str(tmp_path), *args])


class TestScaffold:
    def test_creates_store_layout(self, tmp_path):
        result = run_init(tmp_path)
        assert result.exit_code == 0, result.output
        assert (tmp_path / "knowledge" / "shared").is_dir()
        assert (tmp_path / "knowledge" / "README.md").exists()
        config = yaml.safe_load((tmp_path / ".fixdoc" / "config.yaml").read_text())
        assert config["spec_version"] == 1
        assert config["embedding_model"] == "BAAI/bge-small-en-v1.5"

    def test_gitignores_the_index(self, tmp_path):
        run_init(tmp_path)
        assert ".fixdoc-index/" in (tmp_path / ".gitignore").read_text()

    def test_existing_gitignore_appended_not_clobbered(self, tmp_path):
        (tmp_path / ".gitignore").write_text("node_modules/\n")
        run_init(tmp_path)
        text = (tmp_path / ".gitignore").read_text()
        assert "node_modules/" in text and ".fixdoc-index/" in text


class TestMcpConfig:
    def test_writes_mcp_json(self, tmp_path):
        run_init(tmp_path)
        config = json.loads((tmp_path / ".mcp.json").read_text())
        server = config["mcpServers"]["fixdoc"]
        assert server["command"] == "fixdoc"
        assert server["args"] == ["serve", "--store", "."]

    def test_merges_existing_mcp_json(self, tmp_path):
        (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
        run_init(tmp_path)
        config = json.loads((tmp_path / ".mcp.json").read_text())
        assert "other" in config["mcpServers"]  # untouched
        assert "fixdoc" in config["mcpServers"]

    def test_cursor_harness_writes_cursor_config(self, tmp_path):
        run_init(tmp_path, "--harness", "cursor")
        config = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
        assert "fixdoc" in config["mcpServers"]

    def test_harness_none_skips_configs(self, tmp_path):
        run_init(tmp_path, "--harness", "none")
        assert not (tmp_path / ".mcp.json").exists()
        assert (tmp_path / "knowledge" / "shared").is_dir()  # store still scaffolded


class TestInstructions:
    def test_appends_stanza_to_existing_claude_md(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# My project\n")
        run_init(tmp_path)
        text = (tmp_path / "CLAUDE.md").read_text()
        assert "# My project" in text
        assert "search_fixes" in text and "record_fix" in text and "confirm_fix" in text
        assert not (tmp_path / "AGENTS.md").exists()  # one instruction file, not two

    def test_creates_agents_md_when_no_instruction_file(self, tmp_path):
        run_init(tmp_path)
        assert "search_fixes" in (tmp_path / "AGENTS.md").read_text()

    def test_stanza_idempotent(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# My project\n")
        run_init(tmp_path)
        run_init(tmp_path)
        assert (tmp_path / "CLAUDE.md").read_text().count("search_fixes") == 1

    def test_claude_code_harness_writes_skill(self, tmp_path):
        run_init(tmp_path, "--harness", "claude-code")
        skill = (tmp_path / ".claude" / "skills" / "fixdoc" / "SKILL.md").read_text()
        assert skill.startswith("---")
        assert "description:" in skill
        assert "search_fixes" in skill

    def test_auto_writes_skill_only_when_dot_claude_exists(self, tmp_path):
        run_init(tmp_path)
        assert not (tmp_path / ".claude").exists()
        (tmp_path / ".claude").mkdir()
        run_init(tmp_path)
        assert (tmp_path / ".claude" / "skills" / "fixdoc" / "SKILL.md").exists()


class TestIdempotence:
    def test_second_run_is_clean_noop(self, tmp_path):
        run_init(tmp_path)
        result = run_init(tmp_path)
        assert result.exit_code == 0
        config = json.loads((tmp_path / ".mcp.json").read_text())
        assert list(config["mcpServers"]) == ["fixdoc"]
        assert (tmp_path / ".gitignore").read_text().count(".fixdoc-index/") == 1


class TestServeReadsConfig:
    def test_serve_uses_config_model(self, tmp_path, monkeypatch):
        run_init(tmp_path)
        config_path = tmp_path / ".fixdoc" / "config.yaml"
        config_path.write_text("spec_version: 1\nembedding_model: custom/model-x\n")
        serve_mod = importlib.import_module("fixdoc.commands.serve")
        seen = {}

        def fake_embedder(model_name):
            seen["model"] = model_name
            raise RuntimeError("stop here")

        monkeypatch.setattr(serve_mod, "get_embedder", fake_embedder)
        CliRunner().invoke(create_cli(), ["serve", "--store", str(tmp_path)])
        assert seen["model"] == "custom/model-x"

    def test_serve_flag_overrides_config(self, tmp_path, monkeypatch):
        run_init(tmp_path)
        serve_mod = importlib.import_module("fixdoc.commands.serve")
        seen = {}

        def fake_embedder(model_name):
            seen["model"] = model_name
            raise RuntimeError("stop here")

        monkeypatch.setattr(serve_mod, "get_embedder", fake_embedder)
        CliRunner().invoke(
            create_cli(), ["serve", "--store", str(tmp_path), "--model", "flag/wins"]
        )
        assert seen["model"] == "flag/wins"
