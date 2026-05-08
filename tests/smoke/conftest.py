"""Shared fixtures and helpers for smoke tests."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from dap_runtimes import RuntimeRegistry
from fastapi.testclient import TestClient

POLL_INTERVAL_S = 0.05
POLL_TIMEOUT_S = 10.0


def wait_for_status(
    client: TestClient,
    run_id: str,
    target_statuses: set[str],
    timeout_s: float = POLL_TIMEOUT_S,
) -> dict[str, Any]:
    """Poll run status until it reaches one of target_statuses or times out."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = client.get(f"/runs/{run_id}").json()
        if body["final_status"] in target_statuses:
            return body  # type: ignore[no-any-return]
        time.sleep(POLL_INTERVAL_S)
    raise AssertionError(f"Run {run_id} did not reach {target_statuses} within {timeout_s}s")


def replace_adapter(registry: RuntimeRegistry, adapter: Any) -> None:
    """Replace an existing adapter in the registry (for testing)."""
    registry._adapters[adapter.id] = adapter


def build_subprocess_mock(
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
    side_effect: Exception | None = None,
    pid: int = 12345,
) -> MagicMock:
    """Create an asyncio.Process-shaped mock for ``create_subprocess_exec``.

    Shared across CLI-tool adapter tests (claude_code, codex, gemini_cli) —
    they all need a process whose ``communicate`` is awaitable, ``wait`` is
    awaitable, ``kill`` is sync, and ``returncode``/``pid`` are settable.
    The ``pid`` parameter is overridable but no current test asserts on
    its value; the default suffices.
    """
    process = MagicMock()
    process.returncode = returncode
    process.pid = pid
    if side_effect is not None:
        process.communicate = AsyncMock(side_effect=side_effect)
    else:
        process.communicate = AsyncMock(return_value=(stdout, stderr))
    process.wait = AsyncMock(return_value=returncode)
    process.kill = MagicMock()
    return process
