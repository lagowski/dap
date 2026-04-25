"""dap stop — F1: PID file + signal handling."""

from __future__ import annotations

from rich.console import Console

console = Console()


def stop_command() -> None:
    console.print(
        "[yellow]⚠ stop command — to be implemented in F1 "
        "(needs PID file + process manager)[/yellow]",
    )
