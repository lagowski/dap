"""dap start — odpala FastAPI engine in-process + bundled dashboard."""

from __future__ import annotations

import logging
import os

import uvicorn
from rich.console import Console

from dap_cli.dashboard import find_bundle, find_node, spawn_dashboard
from dap_cli.paths import (
    DEFAULT_DASHBOARD_PORT,
    DEFAULT_ENGINE_PORT,
    local_dap_dir,
    local_db_path,
)
from dap_cli.process import (
    install_pid_cleanup_handlers,
    is_process_alive,
    read_pid_file,
    remove_pid_file,
    write_pid_file,
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

    existing = read_pid_file()
    if existing is not None:
        if is_process_alive(existing["pid"]):
            console.print(
                f"[red]✗ DAP already running (PID {existing['pid']}, "
                f"port {existing['port']}).[/red]",
            )
            console.print("[dim]  Use `dap stop` first if you want to restart.[/dim]")
            raise SystemExit(1)
        console.print(
            f"[yellow]⚠ Stale PID file "
            f"(process {existing['pid']} not running) — cleaning up.[/yellow]",
        )
        remove_pid_file()

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

    write_pid_file(pid=os.getpid(), port=engine_port)
    install_pid_cleanup_handlers()

    console.print("[cyan]Starting DAP...[/cyan]")
    console.print(f"[green]✓ engine[/green]  http://127.0.0.1:{engine_port}")

    # Try to spawn the bundled dashboard. ``spawn_dashboard`` returns
    # None when either the bundle (``_dashboard/server.js``) or
    # ``node`` is missing — both are expected in dev installs that
    # haven't run ``scripts/build-dashboard-bundle.sh``.
    dashboard_proc = spawn_dashboard(
        port=port,
        engine_url=f"http://127.0.0.1:{engine_port}",
    )
    if dashboard_proc is not None:
        console.print(f"[green]✓ dashboard[/green]  http://127.0.0.1:{port}")
    else:
        # Distinguish the two no-dashboard cases so the operator
        # knows which knob to turn.
        if find_bundle() is None:
            reason = (
                "no bundle in this wheel — run "
                "[bold]scripts/build-dashboard-bundle.sh[/bold] from a checkout"
            )
        elif find_node() is None:
            reason = "Node.js not on PATH — install Node 20+ to enable"
        else:
            reason = "unknown (check logs)"
        console.print(
            f"[yellow]○ dashboard[/yellow]  not started [dim]({reason})[/dim]",
        )

    if not headless and dashboard_proc is not None:
        console.print(f"[dim]  Open http://127.0.0.1:{port} in your browser.[/dim]")
    console.print()
    console.print("[dim]Press Ctrl+C to stop.[/dim]")

    try:
        uvicorn.run(
            app,
            host=config.host,
            port=config.port,
            log_level="info",
            access_log=False,
        )
    finally:
        # Stop the dashboard before clearing our PID file so the
        # operator never sees the dashboard outlive ``dap stop``.
        if dashboard_proc is not None and dashboard_proc.poll() is None:
            dashboard_proc.terminate()
            try:
                dashboard_proc.wait(timeout=5)
            except Exception:
                dashboard_proc.kill()
        remove_pid_file()
