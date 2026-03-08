"""CLI assembly for fixdoc."""

import click

from .config import ConfigManager, resolve_base_path
from .commands import capture, search, show, analyze, list_fixes, stats, delete, edit, sync, demo, watch, pending, import_group, resolve


def create_cli() -> click.Group:

    @click.group()
    @click.version_option(version="0.1.0", prog_name="fixdoc")
    @click.pass_context
    def cli(ctx):
        ctx.ensure_object(dict)
        base_path = resolve_base_path()
        config_manager = ConfigManager(base_path)
        ctx.obj["base_path"] = base_path
        ctx.obj["config_manager"] = config_manager
        ctx.obj["config"] = config_manager.load()

    # group commands
    cli.add_command(capture)
    cli.add_command(search)
    cli.add_command(show)
    cli.add_command(analyze)
    cli.add_command(list_fixes)
    cli.add_command(stats)
    cli.add_command(delete)
    cli.add_command(edit)
    cli.add_command(sync)
    cli.add_command(demo)
    cli.add_command(watch)
    cli.add_command(pending)
    cli.add_command(import_group)
    cli.add_command(resolve)

    return cli
