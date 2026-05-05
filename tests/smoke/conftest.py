"""Shared fixtures and helpers for smoke tests."""

from __future__ import annotations

import time
from typing import Any

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
