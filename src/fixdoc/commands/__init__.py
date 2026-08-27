"""CLI commands for fixdoc."""

from .ingest import ingest
from .init_cmd import init_command
from .serve import serve

__all__ = ["ingest", "init_command", "serve"]
