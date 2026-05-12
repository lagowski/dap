"""dap status — pokazuje stan projektu DAP, runtime info gdy działa."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.table import Table

from dap_cli.bootstrap import bootstrap_marker_path, read_bootstrap_marker
from dap_cli.paths import local_dap_dir
from dap_cli.process import (
    is_process_alive,
    read_pid_file,
    remove_pid_file,
    uptime_from_started_at,
)

console = Console()

HTTP_REQUEST_TIMEOUT_SECONDS = 2.0


def _fetch_runtimes(port: int) -> list[dict[str, Any]] | None:
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(timeout=HTTP_REQUEST_TIMEOUT_SECONDS) as client:
            response = client.get(f"{base}/runtimes")
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    return data


def _fetch_runtime_health(port: int, runtime_id: str) -> dict[str, Any] | None:
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(timeout=HTTP_REQUEST_TIMEOUT_SECONDS) as client:
            response = client.get(f"{base}/runtimes/{runtime_id}/health")
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _print_bootstrap_section(dap_dir: Path) -> None:
    """Print admin-bootstrap state (one line) above the engine status.

    Reads ``.dap/bootstrap.json`` (written by ``dap init``). Stays
    silent when it's not present *and* the project is otherwise
    initialised — we don't want to nag every ``dap status`` call when
    the operator hasn't bootstrapped yet, so we just show a hint."""
    marker = read_bootstrap_marker(bootstrap_marker_path(dap_dir))
    if marker is None:
        console.print("[yellow]○ admin bootstrap:[/yellow] none — run `dap init` to create one")
        return
    email = marker.get("email", "?")
    created = marker.get("created_at", "?")
    console.print(f"[green]✓ admin bootstrap:[/green] {email} [dim]({created})[/dim]")


def status_command() -> None:
    dap_dir = local_dap_dir()

    if not dap_dir.exists():
        console.print("[red]✗ Not a DAP project[/red]")
        console.print("[dim]  Run `dap init` to initialize.[/dim]")
        return

    _print_bootstrap_section(dap_dir)

    pid_info = read_pid_file()

    if pid_info is None:
        console.print("[green]✓ DAP project[/green]")
        console.print(f"[dim]  {dap_dir}[/dim]")
        console.print()
        console.print("[yellow]○ engine: stopped[/yellow]")
        console.print("[dim]  Run `dap start` to launch.[/dim]")
        return

    pid = pid_info["pid"]
    port = pid_info["port"]
    started_at = pid_info["started_at"]

    if not is_process_alive(pid):
        console.print("[green]✓ DAP project[/green]")
        console.print(f"[dim]  {dap_dir}[/dim]")
        console.print()
        console.print(f"[red]✗ engine: stale PID file (process {pid} not running)[/red]")
        remove_pid_file()
        console.print("[dim]  Cleaned up. Run `dap start`.[/dim]")
        return

    uptime = uptime_from_started_at(started_at)
    console.print("[green]✓ DAP project[/green]")
    console.print(f"[dim]  {dap_dir}[/dim]")
    console.print()
    console.print(f"[green]● engine: running[/green]  PID {pid}  port {port}  uptime {uptime}")

    runtimes = _fetch_runtimes(port)
    if runtimes is None:
        console.print(
            "[yellow]  ⚠ Engine process running but /runtimes endpoint unreachable.[/yellow]"
        )
        return

    table = Table(title="Runtime adapters", show_lines=False, header_style="bold")
    table.add_column("ID")
    table.add_column("Display name")
    table.add_column("Kind")
    table.add_column("Available")

    for entry in runtimes:
        rt_id = str(entry.get("id", "?"))
        health = _fetch_runtime_health(port, rt_id)
        available = (
            "[green]✓[/green]" if health is not None and health.get("available") else "[red]✗[/red]"
        )
        table.add_row(
            rt_id,
            str(entry.get("displayName", "?")),
            str(entry.get("kind", "?")),
            available,
        )

    console.print()
    console.print(table)
