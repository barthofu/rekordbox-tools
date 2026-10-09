"""Command-line interface for the Rekordbox toolbox."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from rekordbox_toolbox import __version__
from rekordbox_toolbox.m3u8 import apply_playlists, load_config, prepare_playlists

_logger = logging.getLogger(__name__)


@click.group()
@click.version_option(version=__version__)
def cli() -> None:
    """Command-line utilities for Rekordbox."""


@cli.command("m3u8-sync")
@click.argument("config", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option("--dry-run", is_flag=True, help="Show the plan without writing to Rekordbox.")
@click.option("--yes", is_flag=True, help="Apply changes without an interactive confirmation.")
def m3u8_sync(config: Path, dry_run: bool, yes: bool) -> None:
    """Create or replace Rekordbox playlists from M3U8 files."""
    try:
        mappings = load_config(config)
        from pyrekordbox import Rekordbox6Database

        with Rekordbox6Database() as db:
            plans = prepare_playlists(db, mappings)
            _print_plan(plans)
            if dry_run:
                click.echo("Dry run: no changes written.")
                return
            if not yes and not click.confirm("Apply these playlist replacements?", default=False):
                click.echo("Cancelled.")
                return
            changed = apply_playlists(db, plans)
            click.echo(f"Updated {changed} playlist(s).")
    except Exception as exc:
        _logger.error("Playlist sync failed: %s", exc)
        raise click.ClickException(str(exc)) from exc


def _print_plan(plans) -> None:
    for plan in plans:
        state = "replace" if plan.target_exists else "create"
        if not plan.changed:
            state = "unchanged"
        click.echo(f"{state}: {plan.mapping.target} ({len(plan.tracks)} track(s))")
        for path in plan.missing:
            click.echo(f"  not found in Rekordbox: {path}")
        for path in plan.ambiguous:
            click.echo(f"  ambiguous Rekordbox match: {path}")


def main() -> None:
    """Run the CLI with UTF-8 output on Windows consoles."""
    if sys.platform == "win32":
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    cli()
