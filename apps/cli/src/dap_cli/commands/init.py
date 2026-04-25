"""dap init — bootstrap ./.dap/ w bieżącym katalogu."""

from __future__ import annotations

import json

from rich.console import Console

from dap_cli.paths import (
    DEFAULT_DASHBOARD_PORT,
    DEFAULT_ENGINE_PORT,
    local_config_path,
    local_dap_dir,
)

console = Console()


def init_command(force: bool = False) -> None:
    dap_dir = local_dap_dir()
    config_path = local_config_path()

    if dap_dir.exists() and not force:
        console.print(f"[yellow]⚠ .dap/ already exists at {dap_dir}[/yellow]")
        console.print("[dim]  Use --force to reinitialize.[/dim]")
        return

    (dap_dir / "pipelines").mkdir(parents=True, exist_ok=True)
    (dap_dir / "agents").mkdir(parents=True, exist_ok=True)
    (dap_dir / "runs").mkdir(parents=True, exist_ok=True)

    default_config = {
        "version": 1,
        "engine": {"host": "127.0.0.1", "port": DEFAULT_ENGINE_PORT},
        "dashboard": {"port": DEFAULT_DASHBOARD_PORT},
        "runtimes": {"paths": {}},
    }
    config_path.write_text(json.dumps(default_config, indent=2) + "\n", encoding="utf-8")

    console.print("[green]✓ Initialized DAP project[/green]")
    console.print(f"[dim]  {dap_dir}/[/dim]")
    console.print("[dim]  ├─ config.json[/dim]")
    console.print("[dim]  ├─ pipelines/[/dim]")
    console.print("[dim]  ├─ agents/[/dim]")
    console.print("[dim]  └─ runs/[/dim]")
    console.print()
    console.print("[cyan]Next: dap start[/cyan]")
