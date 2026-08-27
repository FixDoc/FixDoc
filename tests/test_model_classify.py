"""Tests for fixdoc.ingestion.model_classify — parsing and failure modes."""

import pytest

from fixdoc.ingestion.model_classify import _parse_keep_response


class TestParseKeepResponse:
    def test_clean_json(self):
        assert _parse_keep_response('[{"i": 0, "keep": true}, {"i": 1, "keep": false}]', 2) == [
            True,
            False,
        ]

    def test_json_wrapped_in_prose(self):
        text = 'Here are my verdicts:\n[{"i": 0, "keep": false}]\nDone.'
        assert _parse_keep_response(text, 1) == [False]

    def test_missing_indices_default_to_keep(self):
        # Conservative on gaps: an unclassified item goes to the queue for a
        # human, it does not silently disappear.
        assert _parse_keep_response('[{"i": 1, "keep": false}]', 3) == [True, False, True]

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            _parse_keep_response("I cannot classify these.", 2)
