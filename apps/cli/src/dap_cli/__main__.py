"""Typer entrypoint — `dap` CLI."""

from __future__ import annotations

import typer

from dap_cli import __version__
from dap_cli.commands.init import init_command
from dap_cli.commands.start import start_command
from dap_cli.commands.status import status_command
from dap_cli.commands.stop import stop_command
from dap_cli.paths import DEFAULT_DASHBOARD_PORT, DEFAULT_ENGINE_PORT

app = typer.Typer(
    name="dap",
    help="Deterministic Agent Pipeline — local launcher",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def root(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
) -> None:
    """Deterministic Agent Pipeline — local launcher."""


@app.command("init")
def cmd_init(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing .dap/"),
) -> None:
    """Initialize DAP project in current directory."""
    init_command(force=force)


@app.command("start")
def cmd_start(
    port: int = typer.Option(DEFAULT_DASHBOARD_PORT, "--port", "-p", help="Dashboard port"),
    engine_port: int = typer.Option(DEFAULT_ENGINE_PORT, "--engine-port", help="Engine port"),
    headless: bool = typer.Option(False, "--headless", help="Do not open browser"),
) -> None:
    """Start engine + dashboard, open browser."""
    start_command(port=port, engine_port=engine_port, headless=headless)


@app.command("stop")
def cmd_stop() -> None:
    """Stop running engine + dashboard."""
    stop_command()


@app.command("status")
def cmd_status() -> None:
    """Show DAP project status."""
    status_command()


if __name__ == "__main__":
    app()
