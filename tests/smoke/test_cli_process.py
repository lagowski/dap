"""Smoke testy dla CLI process management — PID file handling, stop, status."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dap_cli.process import (
    is_process_alive,
    read_pid_file,
    remove_pid_file,
    stop_process,
    uptime_from_started_at,
    write_pid_file,
)


def test_pid_file_roundtrip(tmp_path: Path) -> None:
    pid_path = tmp_path / "dap.pid"
    written = write_pid_file(pid=os.getpid(), port=7333, path=pid_path)
    assert written.exists()

    read = read_pid_file(pid_path)
    assert read is not None
    assert read["pid"] == os.getpid()
    assert read["port"] == 7333
    # ISO 8601 z timezone
    parsed = datetime.fromisoformat(read["started_at"])
    assert parsed.tzinfo is not None


def test_read_pid_file_missing(tmp_path: Path) -> None:
    assert read_pid_file(tmp_path / "nope.pid") is None


def test_read_pid_file_corrupt(tmp_path: Path) -> None:
    pid_path = tmp_path / "dap.pid"
    pid_path.write_text("not json", encoding="utf-8")
    assert read_pid_file(pid_path) is None


def test_read_pid_file_incomplete(tmp_path: Path) -> None:
    pid_path = tmp_path / "dap.pid"
    pid_path.write_text('{"pid": 123}', encoding="utf-8")
    assert read_pid_file(pid_path) is None


def test_remove_pid_file_idempotent(tmp_path: Path) -> None:
    pid_path = tmp_path / "dap.pid"
    write_pid_file(pid=999, port=7333, path=pid_path)
    assert pid_path.exists()
    remove_pid_file(pid_path)
    assert not pid_path.exists()
    # Drugi raz nie wybucha
    remove_pid_file(pid_path)


def test_is_process_alive_self() -> None:
    assert is_process_alive(os.getpid()) is True


def test_is_process_alive_dead_pid() -> None:
    # PID 999999 prawie na pewno nie istnieje
    assert is_process_alive(999_999) is False


def test_stop_process_dead_pid() -> None:
    # Próba zatrzymania nieistniejącego procesu zwraca False, nie wybucha
    assert stop_process(999_999, timeout=0.5) is False


def test_stop_process_real(tmp_path: Path) -> None:
    """Spawn 'sleep' i upewnij się że stop_process go zabija."""
    import subprocess

    proc = subprocess.Popen(["sleep", "30"])
    try:
        # Daj systemowi chwilę na rejestrację procesu
        time.sleep(0.1)
        assert is_process_alive(proc.pid) is True
        terminated = stop_process(proc.pid, timeout=2.0)
        assert terminated is True
        # Po stopie nie żyje
        proc.wait(timeout=2.0)
        assert is_process_alive(proc.pid) is False
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_uptime_format() -> None:
    now = datetime.now(UTC)
    assert uptime_from_started_at(now.isoformat()) in {"0s", "1s"}

    # Stała wartość — relatywnie do "now" więc nie testujemy dokładnej liczby,
    # tylko format.
    formatted = uptime_from_started_at(now.isoformat())
    assert formatted.endswith("s")


def test_uptime_invalid() -> None:
    assert uptime_from_started_at("not-a-date") == "?"


@pytest.mark.parametrize("missing_field", ["pid", "port", "started_at"])
def test_read_pid_file_missing_field(tmp_path: Path, missing_field: str) -> None:
    pid_path = tmp_path / "dap.pid"
    payload = {"pid": 123, "port": 7333, "started_at": "2026-01-01T00:00:00+00:00"}
    del payload[missing_field]
    pid_path.write_text(str(payload).replace("'", '"'), encoding="utf-8")
    assert read_pid_file(pid_path) is None
