"""Tests for the Slack importer engine and CLI — no network, everything injected."""

import importlib

from click.testing import CliRunner

from fixdoc.cli import create_cli
from fixdoc.core.models import Entry
from fixdoc.ingestion.slack_import import import_slack_threads

CH = "C0TEST"

MESSAGES = [
    {
        "ts": "1000.1",
        "text": "checkout is timing out talking to postgres after the failover",
        "user": "U1",
        "reply_count": 2,
    },
    {"ts": "2000.1", "text": "anyone up for lunch?", "user": "U2", "reply_count": 1},
    {
        "ts": "3000.1",
        "text": "ingress 502s after cert rotation, password=hunter2 in the old config",
        "user": "U1",
        "reply_count": 1,
    },
]
REPLIES = {
    "1000.1": [
        {"ts": "1000.2", "text": "it was pointed at the instance endpoint", "user": "U2"},
        {"ts": "1000.3", "text": "repointed at cluster writer, fixed", "user": "U1"},
    ],
    "2000.1": [{"ts": "2000.2", "text": "yes", "user": "U3"}],
    "3000.1": [{"ts": "3000.2", "text": "restarted ingress pods, back up", "user": "U2"}],
}


def fake_fetch_messages(token, channel, oldest=None, **kw):
    return MESSAGES


def fake_fetch_replies(token, channel, ts, **kw):
    return REPLIES.get(ts, [])


def default_extractor(thread_text, rubric):
    if "lunch" in thread_text:
        return {"resolved": False, "confidence": 0.0}
    if "postgres" in thread_text:
        return {
            "resolved": True,
            "confidence": 0.9,
            "title": "Checkout timeouts after RDS failover",
            "symptom": "Timeouts to postgres after failover.",
            "root_cause": "Instance endpoint cached past failover.",
            "fix": "Repoint at the cluster writer endpoint.",
            "verification": "Writes succeeded after restart.",
        }
    return {
        "resolved": True,
        "confidence": 0.4,
        "title": "Ingress 502s after cert rotation",
        "symptom": "502s after rotating certs.",
        "root_cause": None,
        "fix": "Restart ingress pods.",
        "verification": None,
    }


def run_engine(tmp_path, extractor=default_extractor, **kw):
    return import_slack_threads(
        channels=[CH],
        store_dir=tmp_path,
        token="xoxb-test",
        extractor_fn=extractor,
        fetch_messages_fn=fake_fetch_messages,
        fetch_replies_fn=fake_fetch_replies,
        **kw,
    )


def entries_in(tmp_path):
    shared = tmp_path / "knowledge" / "shared"
    return sorted(shared.glob("*.md")) if shared.exists() else []


class TestGate:
    def test_above_threshold_lands_quarantined_with_score(self, tmp_path):
        report = run_engine(tmp_path, threshold=0.6)
        (path,) = entries_in(tmp_path)
        entry = Entry.from_markdown(path.read_text())
        assert entry.status == "quarantined"
        assert entry.confidence == 0.9
        assert "cluster writer" in entry.sections["Fix"]
        assert report.entries_written == 1

    def test_below_threshold_reported_not_written(self, tmp_path):
        report = run_engine(tmp_path, threshold=0.6)
        assert len(report.below_threshold) == 1
        permalink, confidence = report.below_threshold[0]
        assert "3000" in permalink and confidence == 0.4

    def test_lower_threshold_admits_more(self, tmp_path):
        report = run_engine(tmp_path, threshold=0.3)
        assert report.entries_written == 2
        assert report.below_threshold == []

    def test_unresolved_threads_skipped(self, tmp_path):
        report = run_engine(tmp_path)
        assert report.unresolved == 1  # the lunch thread

    def test_rubric_reaches_extractor(self, tmp_path):
        seen = {}

        def extractor(thread_text, rubric):
            seen["rubric"] = rubric
            return {"resolved": False, "confidence": 0.0}

        run_engine(tmp_path, extractor=extractor, rubric="our own scale")
        assert seen["rubric"] == "our own scale"


class TestEntries:
    def test_missing_fields_become_placeholders_not_invention(self, tmp_path):
        run_engine(tmp_path, threshold=0.3)
        low = next(
            p for p in entries_in(tmp_path) if "Ingress" in Entry.from_markdown(p.read_text()).title
        )
        entry = Entry.from_markdown(low.read_text())
        assert "add during review" in entry.sections["Root cause"]
        assert "add during review" in entry.sections["Verification"]

    def test_provenance_notes_carry_channel_and_permalink(self, tmp_path):
        run_engine(tmp_path)
        (path,) = entries_in(tmp_path)
        notes = Entry.from_markdown(path.read_text()).sections["Notes"]
        assert CH in notes and "archives" in notes

    def test_secret_never_reaches_extractor_or_store(self, tmp_path):
        seen_texts = []

        def extractor(thread_text, rubric):
            seen_texts.append(thread_text)
            return default_extractor(thread_text, rubric)

        run_engine(tmp_path, extractor=extractor, threshold=0.3)
        assert all("hunter2" not in t for t in seen_texts)  # redacted BEFORE the model
        for path in entries_in(tmp_path):
            assert "hunter2" not in path.read_text()

    def test_idempotent_rerun(self, tmp_path):
        run_engine(tmp_path)
        report = run_engine(tmp_path)
        assert report.entries_written == 0
        assert len(entries_in(tmp_path)) == 1

    def test_cap_keeps_highest_confidence(self, tmp_path):
        report = run_engine(tmp_path, threshold=0.3, limit=1)
        (path,) = entries_in(tmp_path)
        assert Entry.from_markdown(path.read_text()).confidence == 0.9
        assert report.dropped_by_cap == 1

    def test_dry_run_writes_nothing(self, tmp_path):
        report = run_engine(tmp_path, dry_run=True)
        assert entries_in(tmp_path) == []
        assert report.entries_written == 1  # what WOULD be written


class TestCli:
    def test_config_block_reaches_engine(self, tmp_path, monkeypatch):
        cmd = importlib.import_module("fixdoc.commands.import_slack")
        (tmp_path / ".fixdoc").mkdir()
        (tmp_path / ".fixdoc" / "config.yaml").write_text(
            "spec_version: 1\nimport:\n  model: my-model\n"
            "  confidence_threshold: 0.8\n  confidence_rubric: team scale\n"
            "  api_key_env: TEAM_KEY\n"
        )
        monkeypatch.setenv("SLACK_TOKEN", "xoxb-x")
        monkeypatch.setenv("TEAM_KEY", "k")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        seen = {}

        def fake_engine(**kw):
            seen.update(kw)
            from fixdoc.ingestion.slack_import import SlackImportReport

            return SlackImportReport()

        monkeypatch.setattr(cmd, "import_slack_threads", fake_engine)
        result = CliRunner().invoke(
            create_cli(), ["import-slack", "--channel", CH, "--store", str(tmp_path)]
        )
        assert result.exit_code == 0, result.output
        assert seen["threshold"] == 0.8
        assert seen["rubric"] == "team scale"

    def test_missing_slack_token_is_clear_error(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SLACK_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        result = CliRunner().invoke(
            create_cli(), ["import-slack", "--channel", CH, "--store", str(tmp_path)]
        )
        assert result.exit_code != 0
        assert "SLACK_TOKEN" in result.output

    def test_missing_model_key_is_clear_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SLACK_TOKEN", "xoxb-x")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = CliRunner().invoke(
            create_cli(), ["import-slack", "--channel", CH, "--store", str(tmp_path)]
        )
        assert result.exit_code != 0
        assert "ANTHROPIC_API_KEY" in result.output
