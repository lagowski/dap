"""dap status — pokazuje stan projektu DAP."""

from __future__ import annotations

from rich.console import Console

from dap_cli.paths import local_dap_dir

console = Console()


def status_command() -> None:
    dap_dir = local_dap_dir()

    if not dap_dir.exists():
        console.print("[red]✗ Not a DAP project[/red]")
        console.print("[dim]  Run `dap init` to initialize.[/dim]")
        return

    console.print("[green]✓ DAP project[/green]")
    console.print(f"[dim]  {dap_dir}[/dim]")
    console.print()
    console.print("[yellow]⚠ runtime status check — to be implemented in F1[/yellow]")
