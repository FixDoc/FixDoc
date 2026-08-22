"""Tests for fixdoc.core.events — append-only JSONL event log."""

from fixdoc.core.events import log_event, read_events


class TestEvents:
    def test_round_trip(self, tmp_path):
        log_event(tmp_path, "entry_recorded", {"entry_id": "fx_00000001", "band": "distinct"})
        (event,) = read_events(tmp_path)
        assert event["type"] == "entry_recorded"
        assert event["payload"]["entry_id"] == "fx_00000001"
        assert "ts" in event  # ISO timestamp, exact value not asserted

    def test_appends_in_order(self, tmp_path):
        log_event(tmp_path, "a", {"n": 1})
        log_event(tmp_path, "b", {"n": 2})
        log_event(tmp_path, "c", {"n": 3})
        assert [e["type"] for e in read_events(tmp_path)] == ["a", "b", "c"]

    def test_malformed_line_skipped(self, tmp_path):
        log_event(tmp_path, "good", {})
        with open(tmp_path / "events.jsonl", "a") as f:
            f.write("{truncated by a crash\n")
        log_event(tmp_path, "also_good", {})
        assert [e["type"] for e in read_events(tmp_path)] == ["good", "also_good"]

    def test_no_file_means_no_events(self, tmp_path):
        assert read_events(tmp_path) == []
