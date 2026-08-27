"""CLI assembly for fixdoc.

Two commands, on purpose: init makes a repo agent-ready, serve runs the MCP
server. Agents do the day-to-day work through the four MCP tools; humans
review knowledge in git. Ops commands (status, promote, doctor) join here
as they earn their place.
"""

import click

from .commands import init_command, serve


def create_cli() -> click.Group:
    @click.group()
    @click.version_option(package_name="fixdoc", prog_name="fixdoc")
    def cli():
        """FixDoc — the incident knowledge store your AI agents query over MCP."""

    cli.add_command(init_command)
    cli.add_command(serve)
    return cli
