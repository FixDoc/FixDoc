"""Tests for fixdoc.core.retrieval — filter, blended rank, token budget."""

from datetime import date, timedelta

from fixdoc.core.index import Index
from fixdoc.core.models import Entry
from fixdoc.core.retrieval import search


class SubstringEmbed:
    """Vector chosen by first matching substring; orthogonal default."""

    def __init__(self, table):
        self.table = table

    def __call__(self, text):
        for needle, vector in self.table.items():
            if needle in text:
                return vector
        return [0.0, 1.0]


def write(store, entry, namespace="platform"):
    ns = store / namespace
    ns.mkdir(parents=True, exist_ok=True)
    (ns / f"{entry.id}.md").write_text(entry.to_markdown())


def fix(entry_id, title, symptom="Pods stuck Pending.", **overrides):
    fields = dict(
        id=entry_id,
        type="fix",
        title=title,
        sections={
            "Symptom": symptom,
            "Root cause": "Autoscaler race.",
            "Fix": "Restart the autoscaler.",
            "Verification": "Pods Running.",
        },
        status="validated",
        resource_type="kubernetes/aks",
    )
    fields.update(overrides)
    return Entry(**fields)


def build(tmp_path, entries, embed):
    store = tmp_path / "knowledge"
    for entry in entries:
        write(store, entry)
    index = Index(tmp_path / "idx", embed, "test-model")
    index.sync(store)
    return index, store


QUERY_VEC = {"pods pending": [1.0, 0.0]}


class TestFilter:
    def test_quarantined_excluded_by_default(self, tmp_path):
        embed = SubstringEmbed({"Pods": [1.0, 0.0], "pods pending": [1.0, 0.0]})
        index, store = build(
            tmp_path,
            [
                fix("fx_00000001", "Pending A"),
                fix("fx_00000002", "Pending B", status="quarantined"),
            ],
            embed,
        )
        ids = [r.id for r in search(index, store, "pods pending")]
        assert ids == ["fx_00000001"]
        ids = {r.id for r in search(index, store, "pods pending", include_quarantined=True)}
        assert ids == {"fx_00000001", "fx_00000002"}

    def test_wrong_resource_type_is_a_non_candidate(self, tmp_path):
        # THE trap: identical wording, wrong universe
        embed = SubstringEmbed({"Pods": [1.0, 0.0], "pods pending": [1.0, 0.0]})
        index, store = build(
            tmp_path,
            [
                fix("fx_00000001", "Pending on AKS"),
                fix(
                    "fx_00000002",
                    "Pending on Databricks",
                    resource_type="databricks/jobs",
                ),
            ],
            embed,
        )
        ids = [r.id for r in search(index, store, "pods pending", resource_type="kubernetes/aks")]
        assert ids == ["fx_00000001"]

    def test_env_scope_compatibility(self, tmp_path):
        embed = SubstringEmbed({"Pods": [1.0, 0.0], "pods pending": [1.0, 0.0]})
        index, store = build(
            tmp_path,
            [
                fix("fx_00000001", "Prod only", env_scope=["prod"]),
                fix("fx_00000002", "Any env"),  # empty scope = universal
                fix("fx_00000003", "Staging too", env_scope=["prod", "staging"]),
            ],
            embed,
        )
        ids = {r.id for r in search(index, store, "pods pending", env="staging")}
        assert ids == {"fx_00000002", "fx_00000003"}


class TestRanking:
    def test_higher_similarity_wins(self, tmp_path):
        embed = SubstringEmbed(
            {
                "Close match": [0.9, 0.436],
                "Far match": [0.3, 0.954],
                "pods pending": [1.0, 0.0],
            }
        )
        index, store = build(
            tmp_path,
            [
                fix("fx_00000001", "Far match"),
                fix("fx_00000002", "Close match"),
            ],
            embed,
        )
        results = search(index, store, "pods pending")
        assert [r.id for r in results] == ["fx_00000002", "fx_00000001"]
        assert results[0].similarity > results[1].similarity

    def test_occurrences_break_similarity_ties(self, tmp_path):
        embed = SubstringEmbed({"pods pending": [1.0, 0.0], "Pods": [1.0, 0.0]})
        index, store = build(
            tmp_path,
            [
                fix("fx_00000001", "Never confirmed"),
                fix("fx_00000002", "Battle tested", occurrences=5),
            ],
            embed,
        )
        results = search(index, store, "pods pending")
        assert results[0].id == "fx_00000002"

    def test_match_key_overlap_boosts(self, tmp_path):
        embed = SubstringEmbed({"pods pending": [1.0, 0.0], "Pods": [1.0, 0.0]})
        index, store = build(
            tmp_path,
            [
                fix("fx_00000001", "No keys"),
                fix(
                    "fx_00000002",
                    "Right keys",
                    match_keys={"error_class": "FailedScheduling"},
                ),
            ],
            embed,
        )
        results = search(
            index, store, "pods pending", match_keys={"error_class": "FailedScheduling"}
        )
        assert results[0].id == "fx_00000002"

    def test_stale_entry_loses_tie(self, tmp_path):
        embed = SubstringEmbed({"pods pending": [1.0, 0.0], "Pods": [1.0, 0.0]})
        old = (date.today() - timedelta(days=730)).isoformat()
        index, store = build(
            tmp_path,
            [
                fix("fx_00000001", "Ancient wisdom", created=old),
                fix("fx_00000002", "Fresh fix", created=date.today().isoformat()),
            ],
            embed,
        )
        results = search(index, store, "pods pending")
        assert results[0].id == "fx_00000002"


class TestTokenBudget:
    def test_top_hit_full_then_summaries(self, tmp_path):
        embed = SubstringEmbed({"pods pending": [1.0, 0.0], "Pods": [1.0, 0.0]})
        index, store = build(
            tmp_path,
            [
                fix("fx_00000001", "First"),
                fix("fx_00000002", "Second"),
            ],
            embed,
        )
        results = search(index, store, "pods pending")
        by_detail = {r.detail for r in results}
        assert by_detail == {"full", "summary"}
        full = next(r for r in results if r.detail == "full")
        assert "Root cause" in full.content and "Autoscaler race." in full.content
        summary = next(r for r in results if r.detail == "summary")
        assert "Restart the autoscaler." in summary.content
        assert "Autoscaler race." not in summary.content

    def test_tiny_budget_degrades_to_titles_never_truncates(self, tmp_path):
        embed = SubstringEmbed({"pods pending": [1.0, 0.0], "Pods": [1.0, 0.0]})
        index, store = build(
            tmp_path,
            [
                fix("fx_00000001", "First"),
                fix("fx_00000002", "Second"),
            ],
            embed,
        )
        results = search(index, store, "pods pending", token_budget=10)
        assert all(r.detail == "title" for r in results)
        assert all(r.content == "" for r in results)

    def test_limit_caps_results(self, tmp_path):
        embed = SubstringEmbed({"pods pending": [1.0, 0.0], "Pods": [1.0, 0.0]})
        entries = [fix(f"fx_0000000{i}", f"Fix {i}") for i in range(1, 7)]
        index, store = build(tmp_path, entries, embed)
        assert len(search(index, store, "pods pending", limit=2)) == 2


class TestProvenance:
    def test_result_carries_trust_fields(self, tmp_path):
        embed = SubstringEmbed({"pods pending": [1.0, 0.0], "Pods": [1.0, 0.0]})
        index, store = build(
            tmp_path,
            [
                fix("fx_00000001", "Proven fix", occurrences=3, confidence=0.87),
            ],
            embed,
        )
        (result,) = search(index, store, "pods pending")
        assert result.status == "validated"
        assert result.occurrences == 3
        assert result.confidence == 0.87
        assert 0.99 <= result.similarity <= 1.0
        assert result.score > 0

    def test_no_matches_returns_empty(self, tmp_path):
        embed = SubstringEmbed({"pods pending": [1.0, 0.0], "Pods": [1.0, 0.0]})
        index, store = build(tmp_path, [fix("fx_00000001", "AKS", status="rejected")], embed)
        assert search(index, store, "pods pending") == []
