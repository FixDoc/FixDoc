"""Tests for fixdoc.core.record — the write pipeline behind record_fix/confirm_fix."""

from fixdoc.core.events import read_events
from fixdoc.core.index import Index
from fixdoc.core.models import Entry
from fixdoc.core.record import confirm_entry, record_entry


class LenEmbed:
    """Same search text -> same vector, so identical entries dedup as duplicates."""

    def __call__(self, text):
        return [float(len(text)), 1.0]


class SubstringEmbed:
    """Vector chosen by first matching substring; orthogonal default."""

    def __init__(self, table):
        self.table = table

    def __call__(self, text):
        for needle, vector in self.table.items():
            if needle in text:
                return vector
        return [0.0, 1.0]


def make_fix(title="Pods stuck Pending", **overrides):
    fields = dict(
        id="",
        type="fix",
        title=title,
        sections={
            "Symptom": "Pods pending after scale-up.",
            "Root cause": "Autoscaler race.",
            "Fix": "Restart the autoscaler.",
            "Verification": "All pods Running again.",
        },
        resource_type="kubernetes/aks",
    )
    fields.update(overrides)
    return Entry(**fields)


def build(tmp_path, embed=None):
    index = Index(tmp_path / "idx", embed or LenEmbed(), "test-model")
    return tmp_path / "knowledge", index


def md_files(store):
    return sorted(p.name for p in store.rglob("*.md"))


class TestRecordCreates:
    def test_distinct_entry_creates_quarantined_file(self, tmp_path):
        store, index = build(tmp_path)
        result = record_entry(store, index, make_fix())
        assert result.action == "created"
        assert result.entry_id.startswith("fx_")
        path = store / "shared" / f"{result.entry_id}.md"
        assert path.exists()
        assert Entry.from_markdown(path.read_text()).status == "quarantined"
        assert len(index.candidates("fix")) == 1  # index synced as part of the write

    def test_caller_status_is_overridden(self, tmp_path):
        store, index = build(tmp_path)
        result = record_entry(store, index, make_fix(status="validated"))
        entry = Entry.from_markdown((store / "shared" / f"{result.entry_id}.md").read_text())
        assert entry.status == "quarantined"

    def test_caller_id_is_ignored(self, tmp_path):
        store, index = build(tmp_path)
        result = record_entry(store, index, make_fix(id="fx_hacker01"))
        assert result.entry_id != "fx_hacker01"
        assert not (store / "shared" / "fx_hacker01.md").exists()

    def test_caller_confidence_is_overridden_by_rule_score(self, tmp_path):
        store, index = build(tmp_path)
        result = record_entry(store, index, make_fix(confidence=0.99))
        entry = Entry.from_markdown((store / "shared" / f"{result.entry_id}.md").read_text())
        assert entry.confidence is not None
        assert entry.confidence != 0.99  # short body cannot reach the caller's claim
        assert 0.0 <= entry.confidence <= 1.0

    def test_namespace_param(self, tmp_path):
        store, index = build(tmp_path)
        result = record_entry(store, index, make_fix(), namespace="platform")
        assert (store / "platform" / f"{result.entry_id}.md").exists()

    def test_created_event_logged(self, tmp_path):
        store, index = build(tmp_path)
        result = record_entry(store, index, make_fix())
        (event,) = read_events(index.index_dir)
        assert event["type"] == "entry_recorded"
        assert event["payload"]["action"] == "created"
        assert event["payload"]["entry_id"] == result.entry_id


class TestRecordRejects:
    def test_missing_section_is_invalid(self, tmp_path):
        store, index = build(tmp_path)
        result = record_entry(store, index, make_fix(sections={"Symptom": "Pods pending."}))
        assert result.action == "invalid"
        assert any("Root cause" in p for p in result.problems)
        assert md_files(store) == []  # nothing written


class TestRecordDedup:
    def test_duplicate_confirms_existing_instead_of_writing(self, tmp_path):
        store, index = build(tmp_path)
        first = record_entry(store, index, make_fix())
        result = record_entry(store, index, make_fix())  # identical -> cosine 1.0
        assert result.action == "confirmed_existing"
        assert result.matched_id == first.entry_id
        assert result.similarity > 0.99
        assert len(md_files(store)) == 1  # no second file
        existing = Entry.from_markdown((store / "shared" / f"{first.entry_id}.md").read_text())
        assert existing.occurrences == 1  # bumped from 0

    def test_duplicate_event_preserves_incoming_payload(self, tmp_path):
        store, index = build(tmp_path)
        record_entry(store, index, make_fix())
        record_entry(store, index, make_fix())
        dup_event = read_events(index.index_dir)[-1]
        assert dup_event["payload"]["action"] == "confirmed_existing"
        assert "Pods stuck Pending" in dup_event["payload"]["incoming"]  # nothing lost

    def test_related_band_links_and_creates(self, tmp_path):
        embed = SubstringEmbed({"alpha": [1.0, 0.0], "beta": [0.8, 0.6]})
        store, index = build(tmp_path, embed)
        first = record_entry(store, index, make_fix(title="alpha symptom"))
        result = record_entry(store, index, make_fix(title="beta symptom"))  # cosine 0.8
        assert result.action == "created_related"
        entry = Entry.from_markdown((store / "shared" / f"{result.entry_id}.md").read_text())
        assert entry.related == [first.entry_id]
        assert len(md_files(store)) == 2


class TestConfirm:
    def test_confirm_bumps_occurrences(self, tmp_path):
        store, index = build(tmp_path)
        created = record_entry(store, index, make_fix())
        assert confirm_entry(store, index, created.entry_id) == 1
        assert confirm_entry(store, index, created.entry_id) == 2
        entry = Entry.from_markdown((store / "shared" / f"{created.entry_id}.md").read_text())
        assert entry.occurrences == 2
        assert read_events(index.index_dir)[-1]["type"] == "entry_confirmed"

    def test_confirm_unknown_id_returns_none(self, tmp_path):
        store, index = build(tmp_path)
        assert confirm_entry(store, index, "fx_00000000") is None
