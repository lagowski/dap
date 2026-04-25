"""dap stop — wysyła SIGTERM (potem SIGKILL) procesowi z PID file."""

from __future__ import annotations

from rich.console import Console

from dap_cli.process import is_process_alive, read_pid_file, remove_pid_file, stop_process

console = Console()


def stop_command() -> None:
    pid_info = read_pid_file()
    if pid_info is None:
        console.print("[yellow]⚠ No PID file found — DAP is not running.[/yellow]")
        raise SystemExit(1)

    pid = pid_info["pid"]
    port = pid_info["port"]

    if not is_process_alive(pid):
        console.print(
            f"[yellow]⚠ Stale PID file (process {pid} not running) — cleaning up.[/yellow]",
        )
        remove_pid_file()
        raise SystemExit(1)

    console.print(f"[cyan]Stopping DAP (PID {pid}, port {port})...[/cyan]")
    terminated = stop_process(pid)
    remove_pid_file()

    if terminated:
        console.print("[green]✓ Stopped.[/green]")
    else:
        console.print("[yellow]⚠ Process already exited before SIGTERM.[/yellow]")
