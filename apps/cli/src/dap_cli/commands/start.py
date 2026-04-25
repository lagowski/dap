"""dap start — odpala FastAPI engine in-process."""

from __future__ import annotations

import logging

import uvicorn
from rich.console import Console

from dap_cli.paths import (
    DEFAULT_DASHBOARD_PORT,
    DEFAULT_ENGINE_PORT,
    local_dap_dir,
    local_db_path,
)

console = Console()


def start_command(
    port: int = DEFAULT_DASHBOARD_PORT,
    engine_port: int = DEFAULT_ENGINE_PORT,
    headless: bool = False,
) -> None:
    if not local_dap_dir().exists():
        console.print("[red]✗ No .dap/ found in current directory.[/red]")
        console.print("[dim]  Run `dap init` first.[/dim]")
        raise SystemExit(1)

    # Lazy import — dap --version / --help nie ładują FastAPI/SQLAlchemy
    from dap_engine.app import EngineConfig, create_app  # noqa: PLC0415

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    config = EngineConfig(
        db_path=str(local_db_path()),
        host="127.0.0.1",
        port=engine_port,
    )
    app = create_app(config)

    console.print("[cyan]Starting DAP...[/cyan]")
    console.print(f"[green]✓ engine[/green]  http://127.0.0.1:{engine_port}")
    console.print(
        f"[yellow]○ dashboard[/yellow]  http://127.0.0.1:{port}  "
        "[dim](not yet implemented — F6)[/dim]",
    )
    if not headless:
        console.print("[dim]  (would open browser in --no-headless mode after F6)[/dim]")
    console.print()
    console.print("[dim]Press Ctrl+C to stop.[/dim]")

    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
        access_log=False,
    )
