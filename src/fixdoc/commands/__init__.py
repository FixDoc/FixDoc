"""CLI commands for fixdoc."""

from .capture import capture
from .search import search, show
from .analyze import analyze
from .manage import list_fixes, stats
from .delete import delete
from .edit import edit
from .sync import sync
from .demo import demo
from .watch import watch
from .pending import pending
from .import_cmd import import_group
from .resolve import resolve

__all__ = ["capture", "search", "show", "analyze", "list_fixes", "stats", "delete", "edit", "sync", "demo", "watch", "pending", "import_group", "resolve"]
