"""Tests for fixdoc ingest — day-0 pipeline: docs and logs in, store + queue out."""

import importlib
import shutil
from pathlib import Path

from click.testing import CliRunner

from fixdoc.cli import create_cli
from fixdoc.core.models import Entry

FIXTURES = Path(__file__).parent / "fixtures" / "ingest"


def run_ingest(tmp_path, *sources, args=()):
    src = tmp_path / "input"
    src.mkdir(exist_ok=True)
    for name in sources:
        shutil.copy(FIXTURES / name, src / name)
    return CliRunner().invoke(create_cli(), ["ingest", str(src), "--store", str(tmp_path), *args])


def entries_in(tmp_path):
    return (
        sorted((tmp_path / "knowledge" / "shared").glob("*.md"))
        if (tmp_path / "knowledge" / "shared").exists()
        else []
    )


class TestDocuments:
    def test_full_postmortem_becomes_fix_entry(self, tmp_path):
        result = run_ingest(tmp_path, "postmortem_incident.md")
        assert result.exit_code == 0, result.output
        (path,) = entries_in(tmp_path)
        entry = Entry.from_markdown(path.read_text())
        assert entry.type == "fix"
        assert entry.status == "quarantined"
        assert entry.title == "AKS pods stuck Pending after nodepool scale-up"
        assert "0/14 nodes" in entry.sections["Symptom"]
        assert "Azure CNI reserves" in entry.sections["Root cause"]
        assert "dedicated subnet" in entry.sections["Fix"]
        assert "availableIpAddressCount" in entry.sections["Verification"]

    def test_numbered_steps_become_playbook(self, tmp_path):
        run_ingest(tmp_path, "runbook_rotate.md")
        (path,) = entries_in(tmp_path)
        entry = Entry.from_markdown(path.read_text())
        assert entry.type == "playbook"
        assert path.name.startswith("pb_")
        assert "az aks rotate-certs" in entry.sections["Steps"]

    def test_doc_without_resolution_is_skipped(self, tmp_path):
        result = run_ingest(tmp_path, "meeting_notes.md")
        assert entries_in(tmp_path) == []
        assert "no resolution" in result.output.lower()

    def test_rerun_is_idempotent(self, tmp_path):
        run_ingest(tmp_path, "postmortem_incident.md")
        result = run_ingest(tmp_path, "postmortem_incident.md")
        assert result.exit_code == 0
        assert len(entries_in(tmp_path)) == 1


class TestLogs:
    def test_errors_become_symptom_queue_not_entries(self, tmp_path):
        result = run_ingest(tmp_path, "terraform_apply.log")
        assert result.exit_code == 0, result.output
        assert entries_in(tmp_path) == []  # logs never fabricate fixes
        queue = (tmp_path / ".fixdoc" / "ingest-queue.md").read_text()
        assert "AccessDenied" in queue

    def test_identical_errors_collapse_with_count(self, tmp_path):
        run_ingest(tmp_path, "terraform_apply.log")
        queue = (tmp_path / ".fixdoc" / "ingest-queue.md").read_text()
        assert "2x" in queue  # the AccessDenied block appears twice in the log

    def test_self_explanatory_errors_are_noise(self, tmp_path):
        run_ingest(tmp_path, "noise.log")
        assert not (tmp_path / ".fixdoc" / "ingest-queue.md").exists()


class TestRedaction:
    def test_no_secret_survives_anywhere(self, tmp_path):
        result = run_ingest(tmp_path, "secrets_incident.md")
        assert result.exit_code == 0, result.output
        store_text = "".join(p.read_text() for p in (tmp_path / "knowledge").rglob("*.md"))
        for secret in (
            "AKIAIOSFODNN7EXAMPLE",
            "hunter2",
            "abc123def456ghi789",
            "s3cretpw",
            "ghp_2938471password",
        ):
            assert secret not in store_text
        assert "redact" in result.output.lower()


class TestCapAndModes:
    def test_limit_keeps_highest_substance(self, tmp_path):
        result = run_ingest(
            tmp_path, "postmortem_incident.md", "runbook_rotate.md", args=("--limit", "1")
        )
        paths = entries_in(tmp_path)
        assert len(paths) == 1
        # the full postmortem (4 real sections) outscores the 2-section runbook
        assert paths[0].name.startswith("fx_")
        assert "dropped" in result.output.lower()

    def test_dry_run_writes_nothing(self, tmp_path):
        result = run_ingest(
            tmp_path, "postmortem_incident.md", "terraform_apply.log", args=("--dry-run",)
        )
        assert result.exit_code == 0
        assert entries_in(tmp_path) == []
        assert not (tmp_path / ".fixdoc").exists()


class TestWalkHardening:
    def test_hidden_directories_skipped(self, tmp_path):
        src = tmp_path / "input"
        (src / ".git").mkdir(parents=True)
        shutil.copy(FIXTURES / "postmortem_incident.md", src / ".git" / "leak.md")
        result = CliRunner().invoke(create_cli(), ["ingest", str(src), "--store", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert entries_in(tmp_path) == []

    def test_store_knowledge_never_reingested(self, tmp_path):
        run_ingest(tmp_path, "postmortem_incident.md")
        (path,) = entries_in(tmp_path)
        result = CliRunner().invoke(
            create_cli(), ["ingest", str(tmp_path), "--store", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        assert entries_in(tmp_path) == [path]  # the store's own entries are not candidates


class TestModelClassification:
    def test_model_verdicts_override_rules(self, tmp_path, monkeypatch):
        ingest_mod = importlib.import_module("fixdoc.commands.ingest")

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(ingest_mod, "model_worthiness", lambda items, **kw: [True] * len(items))
        # rules call this noise; the model says keep -> it must reach the queue
        result = run_ingest(tmp_path, "noise.log")
        assert result.exit_code == 0, result.output
        queue = (tmp_path / ".fixdoc" / "ingest-queue.md").read_text()
        assert "MissingRequiredArgument" in queue or "required" in queue.lower()
        assert "model" in result.output.lower()

    def test_api_failure_falls_back_to_rules(self, tmp_path, monkeypatch):
        ingest_mod = importlib.import_module("fixdoc.commands.ingest")

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        def boom(items, **kw):
            raise RuntimeError("api down")

        monkeypatch.setattr(ingest_mod, "model_worthiness", boom)
        result = run_ingest(tmp_path, "terraform_apply.log")
        assert result.exit_code == 0, result.output
        assert "falling back" in result.output.lower()
        queue = (tmp_path / ".fixdoc" / "ingest-queue.md").read_text()
        assert "AccessDenied" in queue  # rules still work

    def test_no_key_uses_rules_silently(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = run_ingest(tmp_path, "terraform_apply.log")
        assert result.exit_code == 0, result.output
        assert "rule-based" in result.output.lower()


class TestClassificationConfig:
    def write_config(self, tmp_path, block):
        cfg = tmp_path / ".fixdoc"
        cfg.mkdir(exist_ok=True)
        (cfg / "config.yaml").write_text("spec_version: 1\n" + block)

    def test_config_names_the_key_env_var(self, tmp_path, monkeypatch):
        ingest_mod = importlib.import_module("fixdoc.commands.ingest")
        self.write_config(tmp_path, "classification:\n  api_key_env: TEAM_LLM_KEY\n")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("TEAM_LLM_KEY", "secret-from-ci")
        seen = {}

        def fake(items, model=None, api_key=None, base_url=None):
            seen.update(model=model, api_key=api_key, base_url=base_url)
            return [True] * len(items)

        monkeypatch.setattr(ingest_mod, "model_worthiness", fake)
        result = run_ingest(tmp_path, "terraform_apply.log")
        assert result.exit_code == 0, result.output
        assert seen["api_key"] == "secret-from-ci"

    def test_config_model_and_base_url_travel_with_the_key(self, tmp_path, monkeypatch):
        ingest_mod = importlib.import_module("fixdoc.commands.ingest")
        self.write_config(
            tmp_path,
            "classification:\n  model: my-gateway-model\n"
            "  api_key_env: GATEWAY_KEY\n  base_url: https://llm.internal/v1\n",
        )
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("GATEWAY_KEY", "gw-key")
        seen = {}

        def fake(items, model=None, api_key=None, base_url=None):
            seen.update(model=model, base_url=base_url)
            return [True] * len(items)

        monkeypatch.setattr(ingest_mod, "model_worthiness", fake)
        run_ingest(tmp_path, "terraform_apply.log")
        assert seen["model"] == "my-gateway-model"
        assert seen["base_url"] == "https://llm.internal/v1"

    def test_flag_overrides_config_model(self, tmp_path, monkeypatch):
        ingest_mod = importlib.import_module("fixdoc.commands.ingest")
        self.write_config(tmp_path, "classification:\n  model: from-config\n")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        seen = {}

        def fake(items, model=None, api_key=None, base_url=None):
            seen.update(model=model)
            return [True] * len(items)

        monkeypatch.setattr(ingest_mod, "model_worthiness", fake)
        run_ingest(tmp_path, "terraform_apply.log", args=("--classify-model", "from-flag"))
        assert seen["model"] == "from-flag"
