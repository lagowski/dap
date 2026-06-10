"""Shared fixtures and helpers for smoke tests."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from dap_engine.app import EngineConfig, create_app
from dap_runtimes import RuntimeRegistry
from fastapi.testclient import TestClient

from tests.smoke._auth import authed_test_client

POLL_INTERVAL_S = 0.05
POLL_TIMEOUT_S = 10.0


# ---------------------------------------------------------------------------
# Auto-marker — unit vs integration (audit X2 + X6)
# ---------------------------------------------------------------------------
#
# Lifts the per-file ``pytestmark`` boilerplate into one collection hook.
# A test file lands on ``UNIT_FILES`` when it has no DB / engine boot /
# subprocess — i.e. it tests a pure helper in isolation. Everything else
# in ``tests/smoke/`` is integration (boots the engine app via the shared
# ``client`` / ``authed_client`` fixture, hits the SQLite DB, or shells
# out to a CLI runtime).
#
# CI can then split:
#   - ``pytest -m unit``         → fast pre-commit / pre-push gate (sub-
#                                  second total today)
#   - ``pytest -m integration``  → slow CI job (the existing smoke run)
#
# Maintenance: when a new test file lands in ``tests/smoke/``, add it
# here only if it qualifies as unit. The default is integration, which
# matches what the smoke suite has always done — a forgotten entry
# means a new test is "merely" slow, not silently un-marked.
UNIT_FILES = frozenset(
    {
        "test_agent_schema_validation.py",
        "test_api_call_adapter.py",
        "test_claude_code_adapter.py",
        "test_cli_base_streaming.py",
        "test_cli_cortex_auth.py",
        "test_cli_http_utils.py",
        "test_codex_adapter.py",
        "test_conditions.py",
        "test_db_url_helpers.py",
        "test_gate_payload_warnings.py",
        "test_gemini_cli_adapter.py",
        "test_http_adapter.py",
        "test_instance_env_vars_merge.py",
        "test_output_parser.py",
        "test_pipeline_state_description.py",
        "test_prompt_builder.py",
        "test_provider_gemini.py",
        "test_provider_openai.py",
        "test_python_func_adapter.py",
        "test_run_registry.py",
        "test_run_state_machine.py",
    }
)


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Auto-apply ``unit`` / ``integration`` markers based on file name.

    Runs after collection, before any test executes. The classification
    is purely file-name based — no AST inspection, no import probing —
    which keeps the hook trivial. Per-test override is still possible:
    if a test explicitly carries ``@pytest.mark.integration`` it stays
    integration even when it lives in a UNIT_FILES file.
    """
    for item in items:
        # ``item.path`` is the test file's Path. We only auto-mark
        # tests under ``tests/smoke/`` — the rest of the testpath
        # tree (``apps/`` / ``packages/`` unit suites) is opt-in.
        try:
            parent = item.path.parent.name
            filename = item.path.name
        except AttributeError:
            continue
        if parent != "smoke":
            continue
        marker_name = "unit" if filename in UNIT_FILES else "integration"
        # Respect a pre-existing explicit marker — don't downgrade an
        # integration tag a test author put there on purpose.
        existing = {mark.name for mark in item.iter_markers()}
        if "unit" in existing or "integration" in existing:
            continue
        item.add_marker(getattr(pytest.mark, marker_name))


# ---------------------------------------------------------------------------
# Engine app + TestClient fixtures (#audit-X1)
# ---------------------------------------------------------------------------
#
# Before #audit-X1 the same ~15-line ``def client()`` fixture was hand-
# rolled in 29 separate test files (sometimes ``TestClient(app)``, sometimes
# wrapped with ``authed_test_client``). Lifting the canonical shape here
# kills the duplication. Tests with custom ``EngineConfig`` overrides
# (Fernet key, OAuth client, etc.) build a one-off fixture using the
# :func:`engine_config_factory` factory instead of repeating the full
# ``tempfile.mkdtemp`` + ``create_app`` boilerplate.


@pytest.fixture
def engine_config_factory(
    tmp_path: Path,
) -> Callable[..., EngineConfig]:
    """Return a callable that builds an ``EngineConfig`` with test defaults.

    Defaults: a fresh sqlite DB under ``tmp_path`` (pytest's per-test
    tmpdir, auto-cleaned) and a deterministic JWT secret. Pass keyword
    overrides to add OAuth client IDs, Fernet keys, CORS origins, etc.

    Example::

        @pytest.fixture
        def client(engine_config_factory):
            config = engine_config_factory(instance_env_vars_key=...)
            app = create_app(config)
            with TestClient(app) as c:
                yield c
    """

    def _make(**overrides: Any) -> EngineConfig:
        defaults: dict[str, Any] = {
            "db_path": str(tmp_path / "state.db"),
            "auth_jwt_secret": "smoke-test-secret-32-chars-padding",
        }
        defaults.update(overrides)
        return EngineConfig(**defaults)

    return _make


@pytest.fixture
def client(
    engine_config_factory: Callable[..., EngineConfig],
) -> Iterator[TestClient]:
    """Plain ``TestClient`` against a fresh engine with default config.

    Use this when the test does its own ``/auth/register`` + ``/auth/jwt/login``
    dance (admin tests, password-reset flows, audit-log tests) — i.e. the
    "no upfront auth" shape. For tests that just want an authenticated
    client out of the box, use :func:`authed_client` instead.

    Tests that need a non-default ``EngineConfig`` (Fernet key, OAuth
    creds, custom CORS list) should declare their own ``client``
    fixture using :func:`engine_config_factory` — fixtures defined in
    a test module override the conftest one for that module.
    """
    app = create_app(engine_config_factory())
    with TestClient(app) as c:
        yield c


@pytest.fixture
def authed_client(
    engine_config_factory: Callable[..., EngineConfig],
) -> Iterator[TestClient]:
    """``TestClient`` pre-authenticated as a non-admin user.

    Wraps :func:`tests.smoke._auth.authed_test_client` so resource-route
    tests (agents, pipelines, projects, runs) don't have to repeat the
    register-then-login boilerplate. Tests that need admin privileges
    flip ``is_superuser=True`` via the async session factory after the
    fixture spins up (see ``test_ownership_agents.py`` for the pattern).
    """
    app = create_app(engine_config_factory())
    with authed_test_client(app) as c:
        yield c


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


def _make_stream_reader(chunks: list[bytes], *, hang: bool = False) -> MagicMock:
    """Build a mock asyncio.StreamReader.

    ``.read(n)`` returns each queued chunk in turn (the ``n`` size hint is
    ignored — tests control chunking explicitly), then ``b""`` at EOF, mirroring
    a real ``StreamReader`` draining a pipe. ``.read()`` (no arg, used for
    stderr) returns the whole remaining buffer at once. When ``hang`` is set
    every read blocks forever, letting ``asyncio.wait_for`` fire a timeout
    exactly as a never-ending subprocess would.
    """
    queue = list(chunks)

    async def _read(n: int = -1) -> bytes:
        if hang:
            await asyncio.Event().wait()  # never returns — wait_for will cancel
        if n < 0:
            # stderr-style: drain everything at once.
            data = b"".join(queue)
            queue.clear()
            return data
        if not queue:
            return b""
        return queue.pop(0)

    reader = MagicMock()
    reader.read = AsyncMock(side_effect=_read)
    return reader


def build_subprocess_mock(
    *,
    stdout: bytes = b"",
    stdout_chunks: list[bytes] | None = None,
    stderr: bytes = b"",
    returncode: int = 0,
    side_effect: Exception | None = None,
    hang: bool = False,
    pid: int = 12345,
) -> MagicMock:
    """Create an asyncio.Process-shaped mock for ``create_subprocess_exec``.

    Shared across CLI-tool adapter tests (claude_code, codex, gemini_cli).

    The CLI subprocess base streams stdout chunk-by-chunk via
    ``process.stdout.read(n)`` and drains stderr via ``process.stderr.read()``
    (#662). The mock therefore exposes async-readable ``stdout`` / ``stderr``
    ``StreamReader`` shims plus a writable, closeable ``stdin``. ``communicate``
    is *also* mocked because the healthcheck path (``_read_cli_version``) still
    uses it.

    Parameters:

    - ``stdout`` — the full stdout as a single chunk (the common case).
    - ``stdout_chunks`` — explicit list of byte chunks to stream in order; when
      given it overrides ``stdout`` so a test can assert chunked delivery.
    - ``stderr`` — the full stderr (drained in one ``read()``).
    - ``side_effect`` — when set (typically ``TimeoutError``), the legacy
      timeout shape: ``communicate`` raises and stdout reads hang so the
      streaming ``wait_for`` also times out. Kept for backwards-compat with
      the pre-streaming timeout tests.
    - ``hang`` — make every stdout/stderr read block forever (for the streaming
      timeout / cancellation tests).
    - ``returncode`` / ``pid`` — settable process attributes.
    """
    process = MagicMock()
    process.returncode = returncode
    process.pid = pid

    should_hang = hang or side_effect is not None
    chunks = stdout_chunks if stdout_chunks is not None else ([stdout] if stdout else [])

    process.stdout = _make_stream_reader(chunks, hang=should_hang)
    process.stderr = _make_stream_reader([stderr] if stderr else [], hang=should_hang)

    # ``stdin`` is written + closed by the base; drain() must be awaitable.
    stdin = MagicMock()
    stdin.write = MagicMock()
    stdin.drain = AsyncMock(return_value=None)
    stdin.close = MagicMock()
    process.stdin = stdin

    # Healthcheck path still calls communicate(); keep the legacy shape.
    if side_effect is not None:
        process.communicate = AsyncMock(side_effect=side_effect)
    else:
        full_stdout = b"".join(chunks)
        process.communicate = AsyncMock(return_value=(full_stdout, stderr))

    process.wait = AsyncMock(return_value=returncode)
    process.kill = MagicMock()
    process.terminate = MagicMock()
    return process
