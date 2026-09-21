"""Failure recovery, embedding reuse, validation, and competing index writers."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from fixdoc.core.database import transaction
from fixdoc.core.events import log_event
from fixdoc.core.index import Index, index_stats
from tests.test_core_index import CountingEmbed, make_fix, write_entry


def snapshot(index):
    with transaction(index.db) as connection:
        return (
            [dict(row) for row in connection.execute("SELECT * FROM entries")],
            dict(connection.execute("SELECT key, value FROM meta")),
        )


def test_unchanged_sync_reads_only_entry_fingerprints(tmp_path):
    store = tmp_path / "knowledge"
    write_entry(store, make_fix(status="validated", env_scope=["prod"]))
    embed = CountingEmbed()
    with Index(tmp_path / "idx", embed, "test") as index:
        index.sync(store)
        before = snapshot(index)[0]
        columns = set()

        def track_reads(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ and table == "entries":
                columns.add(column)
            return sqlite3.SQLITE_OK

        index.db.set_authorizer(track_reads)
        try:
            report = index.sync(store)
        finally:
            index.db.set_authorizer(None)

        assert columns == {"id", "path", "content_hash", "search_hash"}
        assert report["unchanged"] == 1
        assert snapshot(index)[0] == before
        assert len(embed.calls) == 1


@pytest.mark.parametrize("change", ["metadata", "move", "search_text", "rebuild"])
def test_sync_fetches_embeddings_only_for_reuse(tmp_path, change):
    store = tmp_path / "knowledge"
    entry = make_fix()
    path = write_entry(store, entry)
    write_entry(store, make_fix("fx_00000002"))
    embed = CountingEmbed()
    with Index(tmp_path / "idx", embed, "test") as index:
        index.sync(store)
        if change == "metadata":
            entry.status = "validated"
            path.write_text(entry.to_markdown())
        elif change == "move":
            moved = store / "other" / path.name
            moved.parent.mkdir()
            path.rename(moved)
        elif change == "search_text":
            entry.title = "Changed symptom"
            path.write_text(entry.to_markdown())

        queries = []
        index.db.set_trace_callback(queries.append)
        try:
            report = index.sync(store, rebuild=change == "rebuild")
        finally:
            index.db.set_trace_callback(None)

        reads = [query for query in queries if query.startswith("SELECT embedding ")]
        if change in ("metadata", "move"):
            assert reads == [f"SELECT embedding FROM entries WHERE id = '{entry.id}'"]
            assert len(embed.calls) == 2
        else:
            assert reads == []
            assert len(embed.calls) == (4 if change == "rebuild" else 3)
        assert report["updated"] == (2 if change == "rebuild" else 1)
        assert report["unchanged"] == (0 if change == "rebuild" else 1)


@pytest.mark.parametrize("replacement", ["new_model", "empty_store"])
def test_cached_embedding_fetch_retries_if_generation_changed(tmp_path, monkeypatch, replacement):
    store = tmp_path / "knowledge"
    entry = make_fix()
    path = write_entry(store, entry)
    embed = CountingEmbed()
    with Index(tmp_path / "idx", embed, "test") as index:
        index.sync(store)
        entry.status = "validated"
        path.write_text(entry.to_markdown())
        prepare = index._prepare
        raced = False

        def prepare_after_other_writer(*args):
            nonlocal raced
            if not raced:
                raced = True
                model = "new" if replacement == "new_model" else "test"
                other_store = store
                if replacement == "empty_store":
                    other_store = tmp_path / "empty"
                    other_store.mkdir()
                with Index(index.index_dir, lambda text: [1.0, 2.0, 3.0], model) as other:
                    other.sync(other_store)
            return prepare(*args)

        monkeypatch.setattr(index, "_prepare", prepare_after_other_writer)
        report = index.sync(store)

        assert raced
        assert report["added"] == 1
        assert len(embed.calls) == 2
        assert index.live()[0].status == "validated"
        assert len(index.live()[0].vector) == 2
        assert index_stats(index.index_dir)["embedding_model"] == "test"


def test_metadata_body_and_move_reuse_embeddings(tmp_path):
    store = tmp_path / "knowledge"
    entry = make_fix()
    path = write_entry(store, entry)
    embed = CountingEmbed()
    with Index(tmp_path / "idx", embed, "test") as index:
        index.sync(store)
        entry.status = "validated"
        entry.occurrences = 8
        entry.sections["Fix"] = "A revised resolution."
        path.write_text(entry.to_markdown())
        assert index.sync(store)["updated"] == 1
        assert index.live()[0].occurrences == 8
        moved = store / "other" / path.name
        moved.parent.mkdir()
        path.rename(moved)
        report = index.sync(store)
        assert report["updated"] == 1
        assert report["removed"] == 0
        assert index.path_for(entry.id) == f"other/{entry.id}.md"
        assert len(embed.calls) == 1
        assert index.sync(store)["unchanged"] == 1


@pytest.mark.parametrize("rebuild, model", [(False, "old"), (True, "old"), (False, "new")])
def test_failed_embedding_preserves_rows_and_metadata(tmp_path, rebuild, model):
    store = tmp_path / "knowledge"
    path = write_entry(store, make_fix(status="validated"))
    with Index(tmp_path / "idx", CountingEmbed(), "old") as original:
        original.sync(store)
        before = snapshot(original)
        path.write_text(make_fix(title="edited").to_markdown())
        write_entry(store, make_fix("fx_00000002"))
        calls = []

        def fail_second(text):
            calls.append(text)
            if len(calls) == 2:
                raise RuntimeError("embedding failed")
            return [1.0, 2.0]

        with Index(tmp_path / "idx", fail_second, model) as index:
            with pytest.raises(RuntimeError, match="embedding failed"):
                index.sync(store, rebuild=rebuild)
        assert snapshot(original) == before
        assert original.live()[0].title == "Pods stuck Pending"


@pytest.mark.parametrize("rebuild", [False, True])
def test_failure_after_writes_rolls_back_and_connection_recovers(tmp_path, rebuild, monkeypatch):
    store = tmp_path / "knowledge"
    path = write_entry(store, make_fix())
    with Index(tmp_path / "idx", CountingEmbed(), "test") as index:
        index.sync(store)
        before = snapshot(index)
        path.write_text(make_fix(title="new").to_markdown())

        publish = index._publish

        def fail_after_publish(*args):
            publish(*args)
            raise RuntimeError("disk write failed")

        with monkeypatch.context() as patch:
            patch.setattr(index, "_publish", fail_after_publish)
            with pytest.raises(RuntimeError, match="disk write failed"):
                index.sync(store, rebuild=rebuild)
        assert snapshot(index) == before
        assert index.sync(store)["updated"] == 1


def test_schema_rebuild_ddl_is_rolled_back_on_failure(tmp_path, monkeypatch):
    directory = tmp_path / "idx"
    directory.mkdir()
    with sqlite3.connect(directory / "index.db") as db:
        db.execute("CREATE TABLE entries (id TEXT PRIMARY KEY, junk TEXT)")
        db.execute("INSERT INTO entries VALUES ('old', 'keep me')")
        db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO meta VALUES ('schema_version', 'old')")
    store = tmp_path / "knowledge"
    write_entry(store, make_fix())
    with Index(directory, CountingEmbed(), "test") as index:
        publish = index._publish

        def fail_after_publish(*args):
            publish(*args)
            raise RuntimeError("failed after replacing schema")

        with monkeypatch.context() as patch:
            patch.setattr(index, "_publish", fail_after_publish)
            with pytest.raises(RuntimeError, match="replacing schema"):
                index.sync(store)
        with sqlite3.connect(directory / "index.db") as db:
            assert db.execute("SELECT * FROM entries").fetchall() == [("old", "keep me")]
            assert db.execute("SELECT * FROM meta").fetchall() == [("schema_version", "old")]
        assert index_stats(directory)["needs_rebuild"] is True
        assert index.sync(store)["added"] == 1


def test_rebuild_preserves_events_and_quarantine(tmp_path):
    store = tmp_path / "knowledge"
    write_entry(store, make_fix())
    with Index(tmp_path / "idx", CountingEmbed(), "test") as index:
        index.sync(store)
        log_event(index.index_dir, "example", {"value": 1})
        events_path = index.index_dir / "events.jsonl"
        before = events_path.read_bytes()
        index.sync(store, rebuild=True)
        assert events_path.read_bytes() == before
        assert index.live() == []
        assert index.live(include_quarantined=True)[0].status == "quarantined"
        assert index_stats(index.index_dir)["last_run"]["operation"] == "rebuild"


def test_competing_writers_retry_the_committed_generation(tmp_path):
    store = tmp_path / "knowledge"
    write_entry(store, make_fix())
    barrier = Barrier(2)

    def run():
        def embed(text):
            barrier.wait(timeout=10)
            return [1.0, 2.0]

        with Index(tmp_path / "idx", embed, "test") as index:
            return index.sync(store)

    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(lambda _: run(), range(2)))
    assert sorted(report["added"] for report in reports) == [0, 1]
    assert sorted(report["unchanged"] for report in reports) == [0, 1]
    assert index_stats(tmp_path / "idx")["entries"]["total"] == 1


def test_stale_writer_rescans_after_another_writer_commits(tmp_path):
    store = tmp_path / "knowledge"
    write_entry(store, make_fix())
    did_race = False

    def embed(text):
        nonlocal did_race
        if not did_race:
            did_race = True
            write_entry(store, make_fix("fx_00000002"))
            with Index(tmp_path / "idx", CountingEmbed(), "test") as other:
                other.sync(store)
        return [1.0, 2.0]

    with Index(tmp_path / "idx", embed, "test") as index:
        report = index.sync(store)
        assert report["unchanged"] == 2
        assert len(index.candidates("fix")) == 2


def test_duplicate_ids_exclude_all_copies_and_remove_cached_entry(tmp_path):
    store = tmp_path / "knowledge"
    write_entry(store, make_fix())
    with Index(tmp_path / "idx", CountingEmbed(), "test") as index:
        index.sync(store)
        write_entry(store, make_fix(), namespace="other")
        report = index.sync(store)
        assert report["skipped"] == ["other/fx_00000001.md", "platform/fx_00000001.md"]
        assert all("duplicate id" in reason for reason in report["errors"].values())
        assert report["removed"] == 1
        assert index.candidates("fix") == []


@pytest.mark.parametrize(
    "text, reason",
    [
        ("---\nid: [\n---\n", "invalid YAML"),
        ("---\n- list\n---\n", "indices"),
        (make_fix("fx_00000002").to_markdown(), "match the filename"),
        (make_fix(status="oops").to_markdown(), "status"),
        (make_fix(type="unknown").to_markdown(), "type"),
        (make_fix(title=123).to_markdown(), "title"),
        (make_fix(occurrences="many").to_markdown(), "occurrences"),
        (make_fix(confidence=float("nan")).to_markdown(), "confidence"),
        (make_fix(env_scope="prod").to_markdown(), "env_scope"),
        (make_fix(match_keys={"key": []}).to_markdown(), "match_keys"),
        (
            make_fix(resource_type=[])
            .to_markdown()
            .replace("status:", "resource_type: []\nstatus:"),
            "resource_type",
        ),
        (make_fix(sections={"Symptom": "Incomplete."}).to_markdown(), "missing required section"),
    ],
)
def test_malformed_entry_is_removed_with_reason(tmp_path, text, reason):
    store = tmp_path / "knowledge"
    path = write_entry(store, make_fix())
    with Index(tmp_path / "idx", CountingEmbed(), "test") as index:
        index.sync(store)
        path.write_text(text)
        report = index.sync(store)
        assert report["removed"] == 1
        assert reason in report["errors"]["platform/fx_00000001.md"]
        assert index.candidates("fix") == []


def test_missing_store_cannot_erase_committed_index(tmp_path):
    store = tmp_path / "knowledge"
    write_entry(store, make_fix())
    with Index(tmp_path / "idx", CountingEmbed(), "test") as index:
        index.sync(store)
        before = snapshot(index)
        with pytest.raises(FileNotFoundError):
            index.sync(tmp_path / "wrong")
        assert snapshot(index) == before


@pytest.mark.parametrize("vector", [[], [float("inf"), 1], [1, 2, 3]])
def test_invalid_embeddings_do_not_publish_partial_changes(tmp_path, vector):
    store = tmp_path / "knowledge"
    write_entry(store, make_fix())
    with Index(tmp_path / "idx", CountingEmbed(), "test") as index:
        index.sync(store)
        before = snapshot(index)
        write_entry(store, make_fix("fx_00000002"))
        index.embed_fn = lambda text: vector
        with pytest.raises(ValueError, match="Embedding"):
            index.sync(store)
        assert snapshot(index) == before


def test_unreadable_directory_cannot_erase_index(tmp_path, monkeypatch):
    store = tmp_path / "knowledge"
    write_entry(store, make_fix())
    with Index(tmp_path / "idx", CountingEmbed(), "test") as index:
        index.sync(store)
        before = snapshot(index)

        def cannot_walk(root, *, onerror):
            onerror(PermissionError("unreadable namespace"))

        monkeypatch.setattr("fixdoc.core.index.os.walk", cannot_walk)
        with pytest.raises(PermissionError, match="unreadable"):
            index.sync(store)
        assert snapshot(index) == before
