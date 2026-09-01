"""Tests for the extraction response parser — the pure part of the model path."""

import pytest

from fixdoc.ingestion.slack_extract import DEFAULT_IMPORT_MODEL, _parse_extraction


class TestParseExtraction:
    def test_clean_json(self):
        parsed = _parse_extraction(
            '{"resolved": true, "confidence": 0.82, "title": "t", "symptom": "s",'
            ' "root_cause": "r", "fix": "f", "verification": "v"}'
        )
        assert parsed["resolved"] is True
        assert parsed["confidence"] == 0.82
        assert parsed["fix"] == "f"

    def test_json_wrapped_in_prose(self):
        parsed = _parse_extraction('Sure:\n{"resolved": false, "confidence": 0.1}\nDone.')
        assert parsed["resolved"] is False

    def test_missing_fields_are_none(self):
        parsed = _parse_extraction('{"resolved": true, "confidence": 0.7, "fix": "f"}')
        assert parsed["root_cause"] is None
        assert parsed["verification"] is None

    def test_confidence_clamped(self):
        parsed = _parse_extraction('{"resolved": true, "confidence": 1.7, "fix": "f"}')
        assert parsed["confidence"] == 1.0

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            _parse_extraction("I cannot read this thread.")

    def test_default_model_is_the_cheap_tier(self):
        assert DEFAULT_IMPORT_MODEL == "claude-haiku-4-5"
