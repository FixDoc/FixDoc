"""Tests for fixdoc.core.index — incremental SQLite index over knowledge/."""

from fixdoc.core.index import Index
from fixdoc.core.models import Entry


class CountingEmbed:
    """Deterministic fake embedder that records every call."""

    def __init__(self):
        self.calls = []

    def __call__(self, text):
        self.calls.append(text)
        return [float(len(text)), 1.0]


def write_entry(store_dir, entry, namespace="platform"):
    ns = store_dir / namespace
    ns.mkdir(parents=True, exist_ok=True)
    path = ns / f"{entry.id}.md"
    path.write_text(entry.to_markdown())
    return path


def make_fix(entry_id="fx_00000001", title="Pods stuck Pending", **overrides):
    fields = dict(
        id=entry_id, type="fix", title=title,
        sections={"Symptom": "Pods pending.", "Root cause": "Race.",
                  "Fix": "Restart autoscaler.", "Verification": "Pods Running."},
        resource_type="kubernetes/aks",
    )
    fields.update(overrides)
    return Entry(**fields)


class TestSync:
    def test_empty_store(self, tmp_path):
        index = Index(tmp_path / "idx", CountingEmbed(), "test-model")
        stats = index.sync(tmp_path / "knowledge")
        assert stats["added"] == 0
        assert index.candidates("fix") == []

    def test_adds_new_entries(self, tmp_path):
        store = tmp_path / "knowledge"
        write_entry(store, make_fix("fx_00000001"))
        write_entry(store, make_fix("fx_00000002", title="Image pull backoff"))
        index = Index(tmp_path / "idx", CountingEmbed(), "test-model")
        stats = index.sync(store)
        assert stats["added"] == 2
        assert len(index.candidates("fix")) == 2

    def test_unchanged_files_not_reembedded(self, tmp_path):
        store = tmp_path / "knowledge"
        write_entry(store, make_fix())
        embed = CountingEmbed()
        index = Index(tmp_path / "idx", embed, "test-model")
        index.sync(store)
        first_calls = len(embed.calls)
        stats = index.sync(store)
        assert stats["unchanged"] == 1
        assert stats["added"] == 0
        assert len(embed.calls) == first_calls

    def test_modified_file_reembedded(self, tmp_path):
        store = tmp_path / "knowledge"
        path = write_entry(store, make_fix())
        embed = CountingEmbed()
        index = Index(tmp_path / "idx", embed, "test-model")
        index.sync(store)
        first_calls = len(embed.calls)
        path.write_text(make_fix(title="Different symptom now").to_markdown())
        stats = index.sync(store)
        assert stats["updated"] == 1
        assert len(embed.calls) == first_calls + 1

    def test_deleted_file_removed(self, tmp_path):
        store = tmp_path / "knowledge"
        path = write_entry(store, make_fix())
        index = Index(tmp_path / "idx", CountingEmbed(), "test-model")
        index.sync(store)
        path.unlink()
        stats = index.sync(store)
        assert stats["removed"] == 1
        assert index.candidates("fix") == []

    def test_model_change_forces_full_rebuild(self, tmp_path):
        store = tmp_path / "knowledge"
        write_entry(store, make_fix())
        embed = CountingEmbed()
        Index(tmp_path / "idx", embed, "model-a").sync(store)
        first_calls = len(embed.calls)
        stats = Index(tmp_path / "idx", embed, "model-b").sync(store)
        assert stats["added"] == 1  # re-added from scratch
        assert len(embed.calls) == first_calls + 1

    def test_non_entry_files_silently_ignored(self, tmp_path):
        store = tmp_path / "knowledge"
        write_entry(store, make_fix())
        (store / "platform" / "README.md").write_text("# About this namespace")
        (store / "platform" / "notes.md").write_text("no frontmatter here")
        stats = Index(tmp_path / "idx", CountingEmbed(), "test-model").sync(store)
        assert stats["added"] == 1
        assert stats["skipped"] == []

    def test_malformed_entry_file_skipped_not_fatal(self, tmp_path):
        store = tmp_path / "knowledge"
        write_entry(store, make_fix())
        bad = store / "platform" / "fx_deadbeef.md"
        bad.write_text("looks like an entry filename, but no frontmatter")
        stats = Index(tmp_path / "idx", CountingEmbed(), "test-model").sync(store)
        assert stats["added"] == 1
        assert stats["skipped"] == ["platform/fx_deadbeef.md"]

    def test_index_persists_across_instances(self, tmp_path):
        store = tmp_path / "knowledge"
        write_entry(store, make_fix())
        Index(tmp_path / "idx", CountingEmbed(), "test-model").sync(store)
        reopened = Index(tmp_path / "idx", CountingEmbed(), "test-model")
        assert len(reopened.candidates("fix")) == 1


class TestCandidates:
    def test_filters_by_type(self, tmp_path):
        store = tmp_path / "knowledge"
        write_entry(store, make_fix())
        insight = Entry(id="in_00000001", type="insight", title="Shared NAT",
                        sections={"Context": "Same egress IP."})
        write_entry(store, insight)
        index = Index(tmp_path / "idx", CountingEmbed(), "test-model")
        index.sync(store)
        assert [c.id for c, _ in index.candidates("fix")] == ["fx_00000001"]
        assert [c.id for c, _ in index.candidates("insight")] == ["in_00000001"]

    def test_excludes_deprecated_and_rejected(self, tmp_path):
        store = tmp_path / "knowledge"
        write_entry(store, make_fix("fx_00000001", status="deprecated"))
        write_entry(store, make_fix("fx_00000002", status="rejected"))
        write_entry(store, make_fix("fx_00000003", status="quarantined"))
        write_entry(store, make_fix("fx_00000004", status="validated"))
        index = Index(tmp_path / "idx", CountingEmbed(), "test-model")
        index.sync(store)
        ids = sorted(c.id for c, _ in index.candidates("fix"))
        assert ids == ["fx_00000003", "fx_00000004"]

    def test_candidate_carries_fields_dedup_needs(self, tmp_path):
        store = tmp_path / "knowledge"
        write_entry(store, make_fix())
        index = Index(tmp_path / "idx", CountingEmbed(), "test-model")
        index.sync(store)
        (candidate, vector), = index.candidates("fix")
        assert candidate.id == "fx_00000001"
        assert candidate.type == "fix"
        assert candidate.resource_type == "kubernetes/aks"
        assert isinstance(vector, list) and len(vector) == 2

    def test_candidates_work_with_dedup_check(self, tmp_path):
        from fixdoc.core.dedup import dedup_check

        store = tmp_path / "knowledge"
        write_entry(store, make_fix())
        embed = CountingEmbed()
        index = Index(tmp_path / "idx", embed, "test-model")
        index.sync(store)
        incoming = make_fix("fx_99999999")  # same title/symptom -> same fake vector
        decision = dedup_check(incoming, index.candidates("fix"), embed)
        assert decision.band == "duplicate"
        assert decision.matched_id == "fx_00000001"


class TestSchemaHeal:
    def test_old_schema_index_rebuilds_itself(self, tmp_path):
        import sqlite3

        idx_dir = tmp_path / "idx"
        idx_dir.mkdir()
        db = sqlite3.connect(str(idx_dir / "index.db"))
        db.execute("CREATE TABLE entries (id TEXT PRIMARY KEY, junk TEXT)")
        db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO meta VALUES ('embedding_model', 'test-model')")
        db.execute("INSERT INTO entries VALUES ('fx_old00001', 'stale')")
        db.commit()
        db.close()
        index = Index(idx_dir, CountingEmbed(), "test-model")
        assert index.candidates("fix") == []
        store = tmp_path / "knowledge"
        write_entry(store, make_fix())
        assert index.sync(store)["added"] == 1


class TestLive:
    def test_row_carries_retrieval_fields(self, tmp_path):
        store = tmp_path / "knowledge"
        write_entry(store, make_fix(status="validated", occurrences=3, confidence=0.9,
                                    created="2026-08-14", env_scope=["prod"],
                                    match_keys={"error_class": "FailedScheduling"}))
        index = Index(tmp_path / "idx", CountingEmbed(), "test-model")
        index.sync(store)
        (row,) = index.live()
        assert row.id == "fx_00000001"
        assert row.status == "validated"
        assert row.created == "2026-08-14"
        assert row.env_scope == ["prod"]
        assert row.match_keys == {"error_class": "FailedScheduling"}
        assert row.path == "platform/fx_00000001.md"
        assert len(row.vector) == 2

    def test_live_defaults_to_validated_only(self, tmp_path):
        store = tmp_path / "knowledge"
        write_entry(store, make_fix("fx_00000001", status="validated"))
        write_entry(store, make_fix("fx_00000002", status="quarantined"))
        write_entry(store, make_fix("fx_00000003", status="deprecated"))
        index = Index(tmp_path / "idx", CountingEmbed(), "test-model")
        index.sync(store)
        assert [r.id for r in index.live()] == ["fx_00000001"]
        with_quarantined = sorted(r.id for r in index.live(include_quarantined=True))
        assert with_quarantined == ["fx_00000001", "fx_00000002"]
