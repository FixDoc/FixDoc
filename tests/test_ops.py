"""Tests for the ops surface: fixdoc status, promote, doctor."""

import json
from datetime import datetime, timedelta, timezone

from click.testing import CliRunner

from fixdoc.cli import create_cli
from fixdoc.core.models import Entry


def _entry(entry_id, status="validated", occurrences=0, created="2026-08-01", title=None):
    return Entry(
        id=entry_id,
        type="fix",
        title=title or f"Fix {entry_id}",
        status=status,
        occurrences=occurrences,
        created=created,
        sections={"Symptom": "s", "Root cause": "r", "Fix": "f", "Verification": "v"},
    )


def seed_store(tmp_path):
    shared = tmp_path / "knowledge" / "shared"
    shared.mkdir(parents=True)
    for e in [
        _entry("fx_00000001", occurrences=3),
        _entry("fx_00000002", occurrences=1),
        _entry("fx_00000003", status="quarantined", created="2026-08-20"),
        _entry("fx_00000004", status="quarantined", created="2026-09-01"),
        _entry("fx_00000005", status="deprecated"),
    ]:
        (shared / f"{e.id}.md").write_text(e.to_markdown())
    return tmp_path


def seed_events(tmp_path, days_ago=1):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    events = [
        {
            "ts": ts,
            "type": "retrieval_served",
            "payload": {
                "query": "pods pending",
                "served": [{"id": "fx_00000001"}],
                "tokens_served": 800,
            },
        },
        {
            "ts": ts,
            "type": "retrieval_served",
            "payload": {"query": "mystery kafka lag", "served": [], "tokens_served": 0},
        },
        {
            "ts": ts,
            "type": "entry_confirmed",
            "payload": {"entry_id": "fx_00000001", "occurrences": 4},
        },
        {
            "ts": ts,
            "type": "entry_recorded",
            "payload": {"action": "created", "entry_id": "fx_00000009"},
        },
    ]
    idx = tmp_path / ".fixdoc-index"
    idx.mkdir(exist_ok=True)
    (idx / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def run(tmp_path, *args):
    return CliRunner().invoke(create_cli(), [*args, "--store", str(tmp_path)])


class TestStatus:
    def test_counts_by_status(self, tmp_path):
        seed_store(tmp_path)
        result = run(tmp_path, "status")
        assert result.exit_code == 0, result.output
        assert "2 validated" in result.output
        assert "2 quarantined" in result.output
        assert "1 deprecated" in result.output

    def test_oldest_unreviewed_named(self, tmp_path):
        seed_store(tmp_path)
        result = run(tmp_path, "status")
        assert "fx_00000003" in result.output  # oldest quarantined by created date

    def test_activity_from_events(self, tmp_path):
        seed_store(tmp_path)
        seed_events(tmp_path)
        out = run(tmp_path, "status").output
        assert "2 searches" in out
        assert "1 found nothing" in out
        assert "1 confirm" in out
        assert "1 record" in out

    def test_proof_line_sums_occurrences(self, tmp_path):
        seed_store(tmp_path)
        out = run(tmp_path, "status").output
        assert "resolved 4" in out  # 3 + 1 across validated entries

    def test_tokens_served_reported(self, tmp_path):
        seed_store(tmp_path)
        seed_events(tmp_path)
        assert "800" in run(tmp_path, "status").output

    def test_gap_report_lists_empty_queries(self, tmp_path):
        seed_store(tmp_path)
        seed_events(tmp_path)
        out = run(tmp_path, "status").output
        assert "mystery kafka lag" in out

    def test_old_events_outside_window_excluded(self, tmp_path):
        seed_store(tmp_path)
        seed_events(tmp_path, days_ago=45)
        out = run(tmp_path, "status").output
        assert "0 searches" in out

    def test_empty_store_is_sane(self, tmp_path):
        (tmp_path / "knowledge").mkdir()
        result = run(tmp_path, "status")
        assert result.exit_code == 0, result.output


class TestPromote:
    def test_promote_by_id(self, tmp_path):
        seed_store(tmp_path)
        result = run(tmp_path, "promote", "fx_00000003")
        assert result.exit_code == 0, result.output
        entry = Entry.from_markdown(
            (tmp_path / "knowledge" / "shared" / "fx_00000003.md").read_text()
        )
        assert entry.status == "validated"
        assert "commit" in result.output.lower()  # reminds the human to make it a PR

    def test_promote_by_unique_prefix(self, tmp_path):
        seed_store(tmp_path)
        result = run(tmp_path, "promote", "fx_00000004")
        assert result.exit_code == 0
        entry = Entry.from_markdown(
            (tmp_path / "knowledge" / "shared" / "fx_00000004.md").read_text()
        )
        assert entry.status == "validated"

    def test_unknown_id_errors(self, tmp_path):
        seed_store(tmp_path)
        result = run(tmp_path, "promote", "fx_deadbeef")
        assert result.exit_code != 0
        assert "fx_deadbeef" in result.output

    def test_already_validated_is_noop(self, tmp_path):
        seed_store(tmp_path)
        result = run(tmp_path, "promote", "fx_00000001")
        assert result.exit_code == 0
        assert "already" in result.output.lower()


class TestDoctor:
    def test_healthy_store_passes(self, tmp_path):
        seed_store(tmp_path)
        (tmp_path / ".fixdoc").mkdir()
        (tmp_path / ".fixdoc" / "config.yaml").write_text("spec_version: 1\n")
        result = run(tmp_path, "doctor")
        assert result.exit_code == 0, result.output

    def test_invalid_id_filename_warned(self, tmp_path):
        seed_store(tmp_path)
        bad = tmp_path / "knowledge" / "shared" / "fx_mytest01.md"
        bad.write_text(_entry("fx_00000001").to_markdown())
        result = run(tmp_path, "doctor")
        assert "fx_mytest01.md" in result.output
        assert "ignored" in result.output.lower() or "invalid" in result.output.lower()

    def test_filename_id_mismatch_warned(self, tmp_path):
        seed_store(tmp_path)
        rogue = tmp_path / "knowledge" / "shared" / "fx_aaaa1111.md"
        rogue.write_text(_entry("fx_bbbb2222").to_markdown())
        result = run(tmp_path, "doctor")
        assert "fx_aaaa1111" in result.output and "fx_bbbb2222" in result.output

    def test_missing_knowledge_dir_fails(self, tmp_path):
        result = run(tmp_path, "doctor")
        assert result.exit_code != 0
        assert "knowledge" in result.output
