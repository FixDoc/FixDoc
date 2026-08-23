"""Tests for the embedding backend seam and the `fixdoc serve` command."""

import importlib

import pytest
from click.testing import CliRunner

from fixdoc.cli import create_cli
from fixdoc.core.embedding import DEFAULT_MODEL, get_embedder


class TestEmbedding:
    def test_missing_fastembed_gives_install_hint(self):
        try:
            import fastembed  # noqa: F401

            pytest.skip("fastembed installed in this environment")
        except ImportError:
            pass
        with pytest.raises(RuntimeError, match=r"fixdoc\[embed\]"):
            get_embedder()

    def test_default_model_is_pinned(self):
        assert DEFAULT_MODEL == "BAAI/bge-small-en-v1.5"


class TestServeCommand:
    def test_serve_registered(self):
        assert "serve" in create_cli().commands

    def test_serve_without_fastembed_fails_with_hint(self, tmp_path, monkeypatch):
        serve_mod = importlib.import_module("fixdoc.commands.serve")

        def boom(model_name):
            raise RuntimeError('pip install "fixdoc[embed]"')

        monkeypatch.setattr(serve_mod, "get_embedder", boom)
        runner = CliRunner()
        result = runner.invoke(create_cli(), ["serve", "--store", str(tmp_path)])
        assert result.exit_code != 0
        assert "fixdoc[embed]" in result.output
