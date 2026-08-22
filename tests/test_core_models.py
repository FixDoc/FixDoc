"""Tests for fixdoc.core.models — Entry + markdown round-trip per knowledge store spec v1."""

import pytest

from fixdoc.core.models import Entry, new_id


def make_fix(**overrides):
    defaults = dict(
        id="fx_7c2a91e4",
        type="fix",
        title="AKS pods stuck Pending after nodepool scale-up",
        sections={
            "Symptom": "Pods stuck in Pending after scaling the nodepool.",
            "Root cause": "cluster-autoscaler race with node registration.",
            "Fix": "Restart the autoscaler deployment.",
            "Verification": "kubectl get pods shows all Running.",
        },
        resource_type="kubernetes/aks",
        match_keys={"error_class": "FailedScheduling"},
        severity="high",
        env_scope=["prod", "staging"],
    )
    defaults.update(overrides)
    return Entry(**defaults)


class TestNewId:
    def test_fix_prefix(self):
        nid = new_id("fix")
        assert nid.startswith("fx_")
        assert len(nid) == 11  # fx_ + 8 hex
        int(nid[3:], 16)  # parses as hex

    def test_playbook_prefix(self):
        assert new_id("playbook").startswith("pb_")

    def test_insight_prefix(self):
        assert new_id("insight").startswith("in_")

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError):
            new_id("runbook")

    def test_ids_are_unique(self):
        assert new_id("fix") != new_id("fix")


class TestRoundTrip:
    def test_fix_round_trip_preserves_everything(self):
        entry = make_fix(
            status="validated",
            confidence=0.87,
            occurrences=3,
            validated_by="fiyi",
            supersedes="fx_19bb02a1",
            related=["fx_88d1c2aa"],
            created="2026-08-14",
        )
        parsed = Entry.from_markdown(entry.to_markdown())
        assert parsed == entry

    def test_minimal_insight_round_trip(self):
        entry = Entry(
            id="in_ab12cd34",
            type="insight",
            title="Staging shares prod NAT gateway",
            sections={"Context": "Egress IPs are identical across envs."},
        )
        parsed = Entry.from_markdown(entry.to_markdown())
        assert parsed == entry
        assert parsed.status == "quarantined"
        assert parsed.occurrences == 0

    def test_from_markdown_parses_spec_document(self):
        text = (
            "---\n"
            "id: fx_7c2a91e4\n"
            "type: fix\n"
            'title: "AKS pods stuck Pending"\n'
            "status: validated\n"
            "occurrences: 3\n"
            "created: 2026-08-14\n"
            "resource_type: kubernetes/aks\n"
            "match_keys:\n"
            "  error_class: FailedScheduling\n"
            "---\n"
            "\n"
            "## Symptom\n"
            "\n"
            "Pods stuck Pending.\n"
            "\n"
            "## Root cause\n"
            "\n"
            "Autoscaler race.\n"
            "\n"
            "## Fix\n"
            "\n"
            "Restart autoscaler.\n"
            "\n"
            "## Verification\n"
            "\n"
            "All pods Running.\n"
        )
        entry = Entry.from_markdown(text)
        assert entry.id == "fx_7c2a91e4"
        assert entry.type == "fix"
        assert entry.status == "validated"
        assert entry.created == "2026-08-14"  # yaml date coerced back to str
        assert entry.match_keys == {"error_class": "FailedScheduling"}
        assert entry.sections["Fix"] == "Restart autoscaler."

    def test_from_markdown_without_frontmatter_raises(self):
        with pytest.raises(ValueError):
            Entry.from_markdown("## Symptom\n\njust a body\n")


class TestSearchAndReturnText:
    def test_fix_search_text_is_title_plus_symptom(self):
        entry = make_fix()
        text = entry.search_text()
        assert entry.title in text
        assert entry.sections["Symptom"] in text
        assert entry.sections["Fix"] not in text

    def test_fix_return_text_is_fix_plus_verification(self):
        entry = make_fix()
        text = entry.return_text()
        assert entry.sections["Fix"] in text
        assert entry.sections["Verification"] in text
        assert entry.sections["Symptom"] not in text

    def test_playbook_search_text_uses_when_to_use(self):
        entry = Entry(
            id="pb_ab12cd34",
            type="playbook",
            title="Rotate AKS node certs",
            sections={
                "When to use": "Cert expiry alerts firing.",
                "Steps": "1. az aks rotate-certs",
                "Verification": "Nodes Ready.",
            },
        )
        assert "Cert expiry alerts firing." in entry.search_text()
        assert "az aks rotate-certs" not in entry.search_text()
        assert "az aks rotate-certs" in entry.return_text()

    def test_insight_search_and_return_are_body(self):
        entry = Entry(
            id="in_ab12cd34",
            type="insight",
            title="Shared NAT",
            sections={"Context": "Same egress IP everywhere."},
        )
        assert "Same egress IP everywhere." in entry.search_text()
        assert "Same egress IP everywhere." in entry.return_text()


class TestValidate:
    def test_valid_fix_has_no_problems(self):
        assert make_fix().validate() == []

    def test_missing_required_section_reported(self):
        entry = make_fix(sections={"Symptom": "Pods pending."})
        problems = entry.validate()
        assert any("Root cause" in p for p in problems)
        assert any("Verification" in p for p in problems)

    def test_unknown_type_reported(self):
        entry = make_fix(type="runbook", id="rb_12345678")
        assert entry.validate() != []

    def test_id_prefix_mismatch_reported(self):
        entry = make_fix(id="pb_12345678")
        assert any("pb_12345678" in p for p in entry.validate())
