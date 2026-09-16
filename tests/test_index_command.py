"""User-facing indexing, rebuild and read-only observability contracts."""

import importlib
import json

import pytest
from click.testing import CliRunner

from fixdoc.cli import create_cli
from fixdoc.core.database import SCHEMA_VERSION
from fixdoc.core.embedding import DEFAULT_MODEL
from fixdoc.core.index import Index, index_stats
from tests.test_core_index import CountingEmbed, make_fix, write_entry


@pytest.fixture
def backend(monkeypatch):
    module = importlib.import_module("fixdoc.commands.index_cmd")
    embed = CountingEmbed()
    models = []

    def get_embedder(model):
        models.append(model)
        print("model progress")
        return embed

    monkeypatch.setattr(module, "get_embedder", get_embedder)
    return embed, models


def invoke(root, *args):
    return CliRunner().invoke(create_cli(), ["index", "--store", str(root), *args])


def test_build_incremental_and_rebuild(tmp_path, backend):
    embed, models = backend
    write_entry(tmp_path / "knowledge", make_fix())
    first = invoke(tmp_path, "--json")
    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout)["added"] == 1
    assert "model progress" in first.stderr
    second = invoke(tmp_path, "--json")
    assert json.loads(second.stdout)["unchanged"] == 1
    assert len(embed.calls) == 1
    third = invoke(tmp_path, "--rebuild", "--json")
    assert third.exit_code == 0, third.output
    assert json.loads(third.stdout)["updated"] == 1
    assert len(embed.calls) == 2
    assert models == [DEFAULT_MODEL] * 3


def test_default_store_is_working_directory(tmp_path, backend, monkeypatch):
    write_entry(tmp_path / "knowledge", make_fix())
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(create_cli(), ["index"])
    assert result.exit_code == 0, result.output
    assert "1 added" in result.stdout


def test_model_precedence_shared_with_serve(tmp_path, backend):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / ".fixdoc").mkdir()
    (tmp_path / ".fixdoc" / "config.yaml").write_text("embedding_model: configured\n")
    assert invoke(tmp_path).exit_code == 0
    assert invoke(tmp_path, "--model", "override").exit_code == 0
    assert backend[1] == ["configured", "override"]
    serve = importlib.import_module("fixdoc.commands.serve")
    assert serve.resolve_model(tmp_path) == "configured"
    assert serve.resolve_model(tmp_path, "override") == "override"


@pytest.mark.parametrize("config", ["- wrong\n", "embedding_model: [wrong]\n", "bad: [\n"])
def test_bad_config_does_not_create_index(tmp_path, backend, config):
    (tmp_path / "knowledge").mkdir()
    (tmp_path / ".fixdoc").mkdir()
    (tmp_path / ".fixdoc" / "config.yaml").write_text(config)
    result = invoke(tmp_path, "--json")
    assert result.exit_code == 1
    assert "error" in json.loads(result.stdout)
    assert not (tmp_path / ".fixdoc-index").exists()
    assert not backend[1]


def test_missing_store_fails_before_loading_backend(tmp_path, backend):
    result = invoke(tmp_path, "--json")
    assert result.exit_code == 1
    assert "knowledge/" in json.loads(result.stdout)["error"]
    assert not backend[1]
    assert not (tmp_path / ".fixdoc-index").exists()


def test_empty_store_is_valid(tmp_path, backend):
    (tmp_path / "knowledge").mkdir()
    result = invoke(tmp_path, "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["added"] == 0
    assert backend[0].calls == []


def test_stats_on_missing_index_creates_nothing(tmp_path, backend):
    result = invoke(tmp_path, "--stats", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["exists"] is False
    assert not backend[1]
    assert not (tmp_path / ".fixdoc-index").exists()
    assert "Run fixdoc index" in invoke(tmp_path, "--stats").stdout


def test_stats_are_readonly_and_skip_model_and_config(tmp_path, backend):
    write_entry(tmp_path / "knowledge", make_fix(status="validated"))
    write_entry(tmp_path / "knowledge", make_fix("fx_00000002"))
    assert invoke(tmp_path).exit_code == 0
    database = tmp_path / ".fixdoc-index" / "index.db"
    before = (database.read_bytes(), database.stat().st_mtime_ns)
    (tmp_path / ".fixdoc").mkdir()
    (tmp_path / ".fixdoc" / "config.yaml").write_text("invalid: [")
    result = invoke(tmp_path, "--stats", "--json")
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["entries"] == {
        "total": 2,
        "by_type": {"fix": 2},
        "by_status": {"quarantined": 1, "validated": 1},
    }
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["embedding_model"] == DEFAULT_MODEL
    assert report["size_bytes"] == len(before[0])
    assert report["last_run"]["added"] == 2
    assert report["last_run"]["duration_seconds"] >= 0
    assert report["last_run"]["completed_at"].endswith("+00:00")
    human = invoke(tmp_path, "--stats")
    assert "By status:" in human.stdout
    assert "Last committed rebuild:" in human.stdout
    assert (database.read_bytes(), database.stat().st_mtime_ns) == before
    assert len(backend[1]) == 1


@pytest.mark.parametrize("flags", [("--rebuild",), ("--model", "override")])
def test_stats_rejects_mutating_flags(tmp_path, backend, flags):
    result = invoke(tmp_path, "--stats", *flags, "--json")
    assert result.exit_code == 2
    assert "cannot be combined" in json.loads(result.stdout)["error"]
    assert not backend[1]
    assert not (tmp_path / ".fixdoc-index").exists()


def test_skipped_entries_have_diagnostics_and_exit_code(tmp_path, backend):
    write_entry(tmp_path / "knowledge", make_fix())
    bad = tmp_path / "knowledge" / "platform" / "fx_deadbeef.md"
    bad.write_text("not an entry")
    result = invoke(tmp_path, "--json")
    assert result.exit_code == 2
    report = json.loads(result.stdout)
    assert report["added"] == 1
    assert report["skipped"] == ["platform/fx_deadbeef.md"]
    assert "frontmatter" in report["errors"]["platform/fx_deadbeef.md"]
    assert index_stats(tmp_path / ".fixdoc-index")["last_run"]["skipped"] == report["skipped"]
    assert "platform/fx_deadbeef.md:" in invoke(tmp_path).stdout


def test_embedding_failure_is_json_and_preserves_index(tmp_path, backend, monkeypatch):
    path = write_entry(tmp_path / "knowledge", make_fix())
    assert invoke(tmp_path).exit_code == 0
    before = index_stats(tmp_path / ".fixdoc-index")
    path.write_text(make_fix(title="changed").to_markdown())

    def broken(model):
        raise RuntimeError('Install the backend: pip install "fixdoc[embed]"')

    monkeypatch.setattr(
        importlib.import_module("fixdoc.commands.index_cmd"), "get_embedder", broken
    )
    result = invoke(tmp_path, "--rebuild", "--json")
    assert result.exit_code == 1
    assert "fixdoc[embed]" in json.loads(result.stdout)["error"]
    assert index_stats(tmp_path / ".fixdoc-index") == before


def test_stats_handles_special_characters_in_path(tmp_path):
    root = tmp_path / "a repo?#%"
    write_entry(root / "knowledge", make_fix())
    with Index(root / "idx", CountingEmbed(), "test") as index:
        index.sync(root / "knowledge")
    assert index_stats(root / "idx")["entries"]["total"] == 1


def test_corrupt_database_reports_error_without_traceback(tmp_path, backend):
    directory = tmp_path / ".fixdoc-index"
    directory.mkdir()
    (directory / "index.db").write_bytes(b"not sqlite")
    result = invoke(tmp_path, "--stats", "--json")
    assert result.exit_code == 1
    assert "Database operation failed" in json.loads(result.stdout)["error"]
    assert not backend[1]


def test_ingested_entries_index_and_promote_without_reembedding(tmp_path, backend):
    from pathlib import Path

    from fixdoc.core.models import Entry

    fixture = Path(__file__).parent / "fixtures" / "ingest" / "postmortem_incident.md"
    result = CliRunner().invoke(create_cli(), ["ingest", str(fixture), "--store", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert invoke(tmp_path).exit_code == 0
    assert index_stats(tmp_path / ".fixdoc-index")["entries"]["by_status"] == {"quarantined": 1}
    (path,) = (tmp_path / "knowledge").rglob("fx_*.md")
    entry = Entry.from_markdown(path.read_text())
    entry.status = "validated"
    path.write_text(entry.to_markdown())
    assert invoke(tmp_path).exit_code == 0
    assert len(backend[0].calls) == 1
    assert index_stats(tmp_path / ".fixdoc-index")["entries"]["by_status"] == {"validated": 1}
