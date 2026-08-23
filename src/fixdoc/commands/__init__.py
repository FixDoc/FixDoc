"""CLI commands for fixdoc."""

from .import_legacy import import_legacy
from .init_cmd import init_command
from .serve import serve

__all__ = ["import_legacy", "init_command", "serve"]
