"""PID file management + signal handling dla `dap start` / `stop` / `status`."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import psutil

from dap_cli.paths import local_pid_path

GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 5
PID_KILL_POLL_INTERVAL_SECONDS = 0.1
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600


class PidFile(TypedDict):
    pid: int
    port: int
    started_at: str  # ISO 8601 with timezone


def write_pid_file(pid: int, port: int, path: Path | None = None) -> Path:
    pid_path = path or local_pid_path()
    payload: PidFile = {
        "pid": pid,
        "port": port,
        "started_at": datetime.now(UTC).isoformat(),
    }
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return pid_path


def read_pid_file(path: Path | None = None) -> PidFile | None:
    pid_path = path or local_pid_path()
    if not pid_path.exists():
        return None
    try:
        data = json.loads(pid_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    if not all(k in data for k in ("pid", "port", "started_at")):
        return None
    return PidFile(
        pid=int(data["pid"]),
        port=int(data["port"]),
        started_at=str(data["started_at"]),
    )


def remove_pid_file(path: Path | None = None) -> None:
    pid_path = path or local_pid_path()
    pid_path.unlink(missing_ok=True)


def is_process_alive(pid: int) -> bool:
    """Check if process exists AND looks like ours (Python interpreter)."""
    if not psutil.pid_exists(pid):
        return False
    try:
        process = psutil.Process(pid)
        return bool(process.is_running()) and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def stop_process(pid: int, *, timeout: float = GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS) -> bool:
    """Send SIGTERM, wait for graceful exit, fall back to SIGKILL.

    Returns True if process was alive and got terminated, False if already dead.
    """
    if not is_process_alive(pid):
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_process_alive(pid):
            return True
        time.sleep(PID_KILL_POLL_INTERVAL_SECONDS)

    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)

    return True


def install_pid_cleanup_handlers(path: Path | None = None) -> None:
    """Register SIGINT/SIGTERM handlers that remove PID file before exit."""
    pid_path = path or local_pid_path()

    def _handler(signum: int, _frame: object) -> None:
        remove_pid_file(pid_path)
        # Re-raise default handling so uvicorn / asyncio can shutdown cleanly.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def uptime_from_started_at(started_at_iso: str) -> str:
    """Format uptime as human-readable from ISO timestamp."""
    try:
        started = datetime.fromisoformat(started_at_iso)
    except ValueError:
        return "?"
    delta = datetime.now(UTC) - started
    seconds = int(delta.total_seconds())
    if seconds < SECONDS_PER_MINUTE:
        return f"{seconds}s"
    if seconds < SECONDS_PER_HOUR:
        return f"{seconds // SECONDS_PER_MINUTE}m {seconds % SECONDS_PER_MINUTE}s"
    hours = seconds // SECONDS_PER_HOUR
    minutes = (seconds % SECONDS_PER_HOUR) // SECONDS_PER_MINUTE
    return f"{hours}h {minutes}m"
