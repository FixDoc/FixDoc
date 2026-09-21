"""CLI commands for fixdoc."""

from .doctor import doctor
from .import_slack import import_slack
from .index_cmd import index_command
from .ingest import ingest
from .init_cmd import init_command
from .promote import promote
from .serve import serve
from .status import status

__all__ = [
    "doctor",
    "import_slack",
    "index_command",
    "ingest",
    "init_command",
    "promote",
    "serve",
    "status",
]
