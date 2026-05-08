"""dap workspace — workspace management commands."""

from __future__ import annotations

import subprocess
from pathlib import Path

import typer
from rich.console import Console

workspace_app = typer.Typer(
    name="workspace",
    help="Workspace management commands",
    no_args_is_help=True,
)

console = Console()


def workspace_health(clone_path: Path) -> dict:
    """Run health checks on a workspace clone directory.

    Returns a dict with per-check results:
      - clone_exists: bool + message
      - worktree_clean: bool + message
      - remote_reachable: bool + message
    """
    results: dict = {}

    # Check 1: clone directory exists
    if clone_path.is_dir():
        results["clone_exists"] = {"ok": True, "message": f"Clone directory exists: {clone_path}"}
    else:
        results["clone_exists"] = {
            "ok": False,
            "message": f"Clone directory does not exist: {clone_path}",
        }
        # Can't run git checks if directory doesn't exist
        results["worktree_clean"] = {
            "ok": False,
            "message": "Skipped — clone directory does not exist",
        }
        results["remote_reachable"] = {
            "ok": False,
            "message": "Skipped — clone directory does not exist",
        }
        return results

    # Check 2: working tree is clean (no uncommitted changes)
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=clone_path,
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stdout.strip():
            results["worktree_clean"] = {
                "ok": False,
                "message": "Working tree has uncommitted changes",
            }
        else:
            results["worktree_clean"] = {
                "ok": True,
                "message": "Working tree is clean",
            }
    except subprocess.CalledProcessError:
        results["worktree_clean"] = {
            "ok": False,
            "message": "Failed to check git status — is this a git repository?",
        }

    # Check 3: remote is reachable
    try:
        subprocess.run(
            ["git", "ls-remote", "--exit-code", "--heads", "origin"],
            cwd=clone_path,
            capture_output=True,
            text=True,
            check=True,
        )
        results["remote_reachable"] = {
            "ok": True,
            "message": "Git remote is reachable",
        }
    except subprocess.CalledProcessError:
        results["remote_reachable"] = {
            "ok": False,
            "message": "Git remote is not reachable",
        }

    return results


@workspace_app.command("health")
def cmd_health(
    path: Path = typer.Argument(
        ...,
        help="Path to the workspace clone directory",
        exists=False,
    ),
) -> None:
    """Check workspace health: directory exists, worktree clean, remote reachable."""
    results = workspace_health(path)

    all_ok = True
    for check_name, check_result in results.items():
        label = check_name.replace("_", " ").title()
        if check_result["ok"]:
            console.print(f"[green]✓ {label}[/green]: {check_result['message']}")
        else:
            console.print(f"[red]✗ {label}[/red]: {check_result['message']}")
            all_ok = False

    if not all_ok:
        raise typer.Exit(code=1)
