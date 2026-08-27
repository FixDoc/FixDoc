"""Tests for fixdoc ingest — day-0 pipeline: docs and logs in, store + queue out."""

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
