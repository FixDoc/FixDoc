"""Tests for fixdoc import-legacy — old fixes.json -> knowledge entries."""

import json

from click.testing import CliRunner

from fixdoc.cli import create_cli
from fixdoc.core.models import Entry

LEGACY = [
    {
        "id": "ac0c2ba7-7004-4d8f-a2a4-479a59dd4138",
        "issue": "Autoscaler change reduced max surge, causing pod evictions during deploys.",
        "resolution": "Reverted autoscaler flags; added canary for autoscaler config.",
        "tags": ["source:notion:172cc697a80a429fb907f14de9d47a62"],
        "notes": "Source: notion 172cc697 | url: https://notion.so/x",
        "memory_type": "fix",
        "content_hash": "8a1a7f5845132631",
        "created_at": "2026-05-11T06:31:30.647941+00:00",
        "is_private": False,
        "applied_count": 3,
        "success_count": 2,
    },
    {
        "id": "b1",
        "issue": "Verify subnet IP headroom before nodepool scale-ups.",
        "resolution": "az network vnet subnet show ... --query availableIpAddressCount",
        "tags": [],
        "notes": "",
        "memory_type": "check",
        "content_hash": "deadbeef00112233",
        "created_at": "2026-06-01T00:00:00+00:00",
        "is_private": False,
        "applied_count": 0,
        "success_count": 0,
    },
    {
        "id": "c1",
        "issue": "Private thing.",
        "resolution": "Secret resolution.",
        "tags": [],
        "notes": "",
        "memory_type": "fix",
        "content_hash": "aaaa000011112222",
        "created_at": "2026-06-02T00:00:00+00:00",
        "is_private": True,
        "applied_count": 0,
        "success_count": 0,
    },
]


def run_import(tmp_path, *args):
    source = tmp_path / "fixes.json"
    if not source.exists():
        source.write_text(json.dumps({"fixes": LEGACY}))
    return CliRunner().invoke(
        create_cli(),
        ["import-legacy", "--source", str(source), "--store", str(tmp_path), *args],
    )


class TestMapping:
    def test_fix_maps_to_entry_with_placeholders(self, tmp_path):
        result = run_import(tmp_path)
        assert result.exit_code == 0, result.output
        path = tmp_path / "knowledge" / "shared" / "fx_8a1a7f58.md"  # id from content_hash
        entry = Entry.from_markdown(path.read_text())
        assert entry.type == "fix"
        assert entry.status == "quarantined"
        assert entry.title.startswith("Autoscaler change")
        assert entry.sections["Symptom"].startswith("Autoscaler change")
        assert "Reverted autoscaler flags" in entry.sections["Fix"]
        assert "add during review" in entry.sections["Root cause"]
        assert "add during review" in entry.sections["Verification"]
        assert entry.occurrences == 2  # success_count carries over
        assert entry.created == "2026-05-11"
        assert "notion" in entry.sections["Notes"]

    def test_check_becomes_playbook(self, tmp_path):
        run_import(tmp_path)
        path = tmp_path / "knowledge" / "shared" / "pb_deadbeef.md"
        entry = Entry.from_markdown(path.read_text())
        assert entry.type == "playbook"
        assert "Verify subnet IP headroom" in entry.sections["When to use"]
        assert "availableIpAddressCount" in entry.sections["Steps"]

    def test_private_fixes_skipped(self, tmp_path):
        result = run_import(tmp_path)
        assert "1 private" in result.output
        assert not list((tmp_path / "knowledge").rglob("*aaaa0000*"))
        for path in (tmp_path / "knowledge" / "shared").glob("*.md"):
            assert "Secret resolution" not in path.read_text()


class TestModes:
    def test_rerun_is_idempotent(self, tmp_path):
        run_import(tmp_path)
        result = run_import(tmp_path)
        assert result.exit_code == 0
        assert "0 imported" in result.output
        assert len(list((tmp_path / "knowledge" / "shared").glob("*.md"))) == 2

    def test_trust_flag_imports_validated(self, tmp_path):
        run_import(tmp_path, "--trust")
        entry = Entry.from_markdown(
            (tmp_path / "knowledge" / "shared" / "fx_8a1a7f58.md").read_text()
        )
        assert entry.status == "validated"

    def test_summary_output(self, tmp_path):
        result = run_import(tmp_path)
        assert "2 imported" in result.output
        assert "quarantined" in result.output


class TestNullFields:
    def test_null_notes_and_tags_survive(self, tmp_path):
        legacy = [dict(LEGACY[0], id="n1", content_hash="1234abcd9999ffff", notes=None, tags=None)]
        source = tmp_path / "fixes.json"
        source.write_text(json.dumps({"fixes": legacy}))
        result = CliRunner().invoke(
            create_cli(),
            ["import-legacy", "--source", str(source), "--store", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "knowledge" / "shared" / "fx_1234abcd.md").exists()

    def test_missing_content_hash_gets_deterministic_id(self, tmp_path):
        legacy = [dict(LEGACY[0], id="old1")]
        del legacy[0]["content_hash"]
        source = tmp_path / "fixes.json"
        source.write_text(json.dumps({"fixes": legacy}))
        args = ["import-legacy", "--source", str(source), "--store", str(tmp_path)]
        assert CliRunner().invoke(create_cli(), args).exit_code == 0
        (first,) = list((tmp_path / "knowledge" / "shared").glob("fx_*.md"))
        result = CliRunner().invoke(create_cli(), args)  # rerun: same id, no dup
        assert "0 imported" in result.output
        assert list((tmp_path / "knowledge" / "shared").glob("fx_*.md")) == [first]
