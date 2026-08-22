"""Tests for fixdoc.core.dedup — three-band similarity gate, pure logic."""

from fixdoc.core.dedup import DedupDecision, dedup_check
from fixdoc.core.models import Entry


def make_entry(
    entry_id="fx_00000001", entry_type="fix", resource_type="kubernetes/aks"
):
    sections = {
        "fix": {"Symptom": "s", "Root cause": "r", "Fix": "f", "Verification": "v"},
        "playbook": {"When to use": "w", "Steps": "s", "Verification": "v"},
        "insight": {"Context": "c"},
    }[entry_type]
    return Entry(
        id=entry_id,
        type=entry_type,
        title="t",
        sections=sections,
        resource_type=resource_type,
    )


def const_embed(text):
    """Incoming entry always embeds to the x-axis."""
    return [1.0, 0.0]


def vec(cosine_with_x):
    """A unit vector whose cosine with [1, 0] is exactly cosine_with_x."""
    return [cosine_with_x, (1.0 - cosine_with_x**2) ** 0.5]


class TestBands:
    def test_no_candidates_is_distinct(self):
        decision = dedup_check(make_entry(), [], const_embed)
        assert decision == DedupDecision("distinct", None, 0.0)

    def test_high_similarity_is_duplicate(self):
        cand = make_entry("fx_00000002")
        decision = dedup_check(make_entry(), [(cand, vec(0.95))], const_embed)
        assert decision.band == "duplicate"
        assert decision.matched_id == "fx_00000002"
        assert decision.similarity > 0.92

    def test_middle_band_is_related(self):
        cand = make_entry("fx_00000002")
        decision = dedup_check(make_entry(), [(cand, vec(0.85))], const_embed)
        assert decision.band == "related"
        assert decision.matched_id == "fx_00000002"

    def test_low_similarity_is_distinct_but_reports_best(self):
        cand = make_entry("fx_00000002")
        decision = dedup_check(make_entry(), [(cand, vec(0.3))], const_embed)
        assert decision.band == "distinct"
        assert decision.matched_id == "fx_00000002"  # near-miss kept for events log

    def test_boundaries_are_inclusive(self):
        cand = make_entry("fx_00000002")
        # 0.25/0.75 chosen because they are exact in binary floating point
        at_dup = dedup_check(
            make_entry(),
            [(cand, vec(0.75))],
            const_embed,
            duplicate_threshold=0.75,
            related_threshold=0.25,
        )
        assert at_dup.band == "duplicate"
        at_rel = dedup_check(
            make_entry(),
            [(cand, vec(0.25))],
            const_embed,
            duplicate_threshold=0.75,
            related_threshold=0.25,
        )
        assert at_rel.band == "related"


class TestScoping:
    def test_other_type_excluded(self):
        cand = make_entry("in_00000002", entry_type="insight")
        decision = dedup_check(make_entry(), [(cand, vec(0.99))], const_embed)
        assert decision.band == "distinct"
        assert decision.matched_id is None

    def test_other_resource_type_excluded(self):
        cand = make_entry("fx_00000002", resource_type="databricks/jobs")
        decision = dedup_check(make_entry(), [(cand, vec(0.99))], const_embed)
        assert decision.band == "distinct"
        assert decision.matched_id is None

    def test_candidate_without_resource_type_still_compared(self):
        cand = make_entry("fx_00000002", resource_type=None)
        decision = dedup_check(make_entry(), [(cand, vec(0.95))], const_embed)
        assert decision.band == "duplicate"

    def test_entry_without_resource_type_compares_against_all(self):
        cand = make_entry("fx_00000002")
        decision = dedup_check(
            make_entry(resource_type=None), [(cand, vec(0.95))], const_embed
        )
        assert decision.band == "duplicate"


class TestBestMatch:
    def test_best_of_several_wins(self):
        near = make_entry("fx_00000002")
        far = make_entry("fx_00000003")
        decision = dedup_check(
            make_entry(), [(far, vec(0.80)), (near, vec(0.95))], const_embed
        )
        assert decision.band == "duplicate"
        assert decision.matched_id == "fx_00000002"
