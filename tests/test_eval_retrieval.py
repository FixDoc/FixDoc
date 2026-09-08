"""Tests for the retrieval eval runner — metrics computed against known ground truth."""

import yaml
from click.testing import CliRunner

from fixdoc.cli import create_cli
from fixdoc.core.models import Entry
from fixdoc.evals.retrieval import run_retrieval_eval


def embed(text):
    # Substring-keyed fake: deterministic, controllable neighborhoods.
    table = {"subnet": [1.0, 0.0], "quota": [0.0, 1.0], "grew the cluster": [0.9, 0.44]}
    for needle, vec in table.items():
        if needle in text:
            return vec
    return [0.5, 0.5]


def seed(tmp_path):
    shared = tmp_path / "store" / "knowledge" / "shared"
    shared.mkdir(parents=True)
    entries = [
        ("fx_5ab8e001", "Pods pending: subnet exhausted", "subnet out of IPs"),
        ("fx_c07a0001", "Databricks jobs pending on quota", "cluster quota exhausted"),
    ]
    for entry_id, title, symptom in entries:
        e = Entry(
            id=entry_id,
            type="fix",
            title=title,
            status="validated",
            sections={"Symptom": symptom, "Root cause": "r", "Fix": "f", "Verification": "v"},
        )
        (shared / f"{entry_id}.md").write_text(e.to_markdown())
    return tmp_path / "store"


def write_cases(tmp_path, cases):
    path = tmp_path / "cases.yaml"
    path.write_text(yaml.safe_dump(cases))
    return path


class TestMetrics:
    def test_hit_counts_toward_recall(self, tmp_path):
        store = seed(tmp_path)
        cases = write_cases(
            tmp_path,
            [
                {
                    "query": "workloads pending after we grew the cluster",
                    "relevant": ["fx_5ab8e001"],
                },
            ],
        )
        report = run_retrieval_eval(cases, store, embed_fn=embed)
        assert report.cases == 1
        assert report.hits == 1
        assert report.recall == 1.0

    def test_miss_is_reported_with_what_came_back(self, tmp_path):
        store = seed(tmp_path)
        cases = write_cases(
            tmp_path,
            [
                {"query": "quota exhausted", "relevant": ["fx_5ab8e001"]},  # wrong label on purpose
            ],
        )
        # k=1: with a tiny store everything fits in top-3, so rank must decide
        report = run_retrieval_eval(cases, store, embed_fn=embed, k=1)
        assert report.hits == 0
        (miss,) = report.misses
        assert miss["query"] == "quota exhausted"
        assert "fx_c07a0001" in miss["top"]

    def test_trap_fires_when_forbidden_id_surfaces(self, tmp_path):
        store = seed(tmp_path)
        cases = write_cases(
            tmp_path,
            [
                {
                    "query": "quota exhausted",
                    "relevant": ["fx_c07a0001"],
                    "must_not_return": ["fx_5ab8e001"],
                },
            ],
        )
        report = run_retrieval_eval(cases, store, embed_fn=embed)
        # fx_5ab8e001 appears in results (only 2 entries, both returned) -> trap fires
        assert report.trap_cases == 1
        assert report.traps_fired == 1

    def test_trap_clean_when_filtered_out(self, tmp_path):
        store = seed(tmp_path)
        # resource_type filter keeps the forbidden entry out entirely
        shared = store / "knowledge" / "shared"
        entry = Entry.from_markdown((shared / "fx_5ab8e001.md").read_text())
        entry.resource_type = "kubernetes/aks"
        (shared / "fx_5ab8e001.md").write_text(entry.to_markdown())
        entry = Entry.from_markdown((shared / "fx_c07a0001.md").read_text())
        entry.resource_type = "databricks/jobs"
        (shared / "fx_c07a0001.md").write_text(entry.to_markdown())
        cases = write_cases(
            tmp_path,
            [
                {
                    "query": "quota exhausted",
                    "resource_type": "databricks/jobs",
                    "relevant": ["fx_c07a0001"],
                    "must_not_return": ["fx_5ab8e001"],
                },
            ],
        )
        report = run_retrieval_eval(cases, store, embed_fn=embed)
        assert report.traps_fired == 0
        assert report.hits == 1

    def test_acceptable_does_not_count_as_relevant(self, tmp_path):
        store = seed(tmp_path)
        cases = write_cases(
            tmp_path,
            [
                {
                    "query": "quota exhausted",
                    "relevant": ["fx_5ab8e001"],
                    "acceptable": ["fx_c07a0001"],
                },
            ],
        )
        report = run_retrieval_eval(cases, store, embed_fn=embed, k=1)
        assert report.hits == 0  # acceptable is not a substitute for relevant
        assert report.acceptable_only == 1  # but it is reported


class TestCli:
    def test_command_prints_metrics(self, tmp_path, monkeypatch):
        store = seed(tmp_path)
        cases = write_cases(
            tmp_path,
            [
                {
                    "query": "workloads pending after we grew the cluster",
                    "relevant": ["fx_5ab8e001"],
                },
            ],
        )
        import importlib

        cmd = importlib.import_module("fixdoc.commands.eval_cmd")
        monkeypatch.setattr(cmd, "_embedder", lambda: embed)
        result = CliRunner().invoke(
            create_cli(), ["eval", "retrieval", "--cases", str(cases), "--store", str(store)]
        )
        assert result.exit_code == 0, result.output
        assert "Recall@3" in result.output
        assert "1/1" in result.output
        assert "Trap" in result.output

    def test_missing_cases_file_is_clear_error(self, tmp_path):
        result = CliRunner().invoke(
            create_cli(),
            ["eval", "retrieval", "--cases", str(tmp_path / "nope.yaml"), "--store", str(tmp_path)],
        )
        assert result.exit_code != 0
