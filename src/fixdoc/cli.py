"""CLI assembly for fixdoc.

Init prepares a repo, ingest seeds knowledge, index manages derived state,
and serve runs MCP. Agents do day-to-day work through the four MCP tools;
humans review knowledge in git.
"""

import click

from .commands import (
    doctor,
    import_slack,
    index_command,
    ingest,
    init_command,
    promote,
    serve,
    status,
)


def create_cli() -> click.Group:
    @click.group()
    @click.version_option(package_name="fixdoc", prog_name="fixdoc")
    def cli():
        """FixDoc — the incident knowledge store your AI agents query over MCP."""

    cli.add_command(init_command)
    cli.add_command(serve)
    cli.add_command(ingest)
    cli.add_command(index_command)
    cli.add_command(import_slack)
    cli.add_command(status)
    cli.add_command(promote)
    cli.add_command(doctor)
    return cli
