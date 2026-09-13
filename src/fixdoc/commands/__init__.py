"""CLI commands for fixdoc."""

from .index_cmd import index_command
from .ingest import ingest
from .init_cmd import init_command
from .serve import serve

__all__ = ["ingest", "index_command", "init_command", "serve"]
