"""Standardowe ścieżki projektu DAP."""

from __future__ import annotations

from pathlib import Path

LOCAL_DAP_DIR_NAME = ".dap"
USER_DAP_DIR = Path.home() / ".dap"

DEFAULT_ENGINE_PORT = 7333
DEFAULT_DASHBOARD_PORT = 7332


def local_dap_dir(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / LOCAL_DAP_DIR_NAME


def local_config_path(cwd: Path | None = None) -> Path:
    return local_dap_dir(cwd) / "config.json"


def local_db_path(cwd: Path | None = None) -> Path:
    return local_dap_dir(cwd) / "state.db"


def local_pid_path(cwd: Path | None = None) -> Path:
    return local_dap_dir(cwd) / "dap.pid"
