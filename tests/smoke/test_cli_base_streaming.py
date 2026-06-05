"""Streaming-stdout tests for the shared CLI subprocess base (#662, Phase 3b-2a).

``_BaseCliAdapter._run_subprocess`` gained an optional ``on_output`` callback
that is invoked with each incremental stdout chunk as the subprocess produces
it. These tests exercise the streaming contract directly against
``_run_subprocess`` (bypassing the JSON-parsing ``execute`` pipeline) so the
assertions stay focused on the drain/callback behaviour:

- chunks are delivered in order, and the returned full stdout still equals the
  concatenation of every chunk;
- ``on_output=None`` (the default, and what real runs pass today) is identical
  to the pre-streaming behaviour — full stdout captured, no callback;
- a raising ``on_output`` is logged and swallowed, never failing the run;
- the timeout path still kills the subprocess and returns a ``timed_out``
  outcome.

The subprocess is mocked via :func:`tests.smoke.conftest.build_subprocess_mock`,
patched at ``dap_runtimes.adapters._cli_base.asyncio.create_subprocess_exec``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from dap_runtimes.adapters._cli_base import _BaseCliAdapter

from .conftest import build_subprocess_mock

_PATCH_PATH = "dap_runtimes.adapters._cli_base.asyncio.create_subprocess_exec"


class _ProbeAdapter(_BaseCliAdapter):
    """Minimal concrete adapter so we can call the shared ``_run_subprocess``."""

    id = "probe"
    display_name = "Probe"
    default_binary = "probe-bin"
    provider_name = "probe"
    display_label = "Probe"


async def _run(
    proc: object,
    *,
    on_output: object = None,
    stdin: bytes = b"<prompt/>",
    timeout_seconds: float = 30.0,
) -> object:
    adapter = _ProbeAdapter()
    with patch(_PATCH_PATH, AsyncMock(return_value=proc)):
        return await adapter._run_subprocess(
            argv=["probe-bin"],
            stdin=stdin,
            cwd="/tmp",
            env={},
            timeout_seconds=timeout_seconds,
            binary="probe-bin",
            on_output=on_output,  # type: ignore[arg-type]
        )


async def test_on_output_receives_each_chunk_in_order() -> None:
    proc = build_subprocess_mock(stdout_chunks=[b"hello ", b"world", b"!"])
    received: list[str] = []

    outcome = await _run(proc, on_output=received.append)

    assert received == ["hello ", "world", "!"]
    # Full stdout is the concatenation of every chunk, independent of streaming.
    assert outcome.stdout == "hello world!"  # type: ignore[attr-defined]
    assert outcome.exit_code == 0  # type: ignore[attr-defined]


async def test_on_output_does_not_affect_returned_stdout_or_stderr() -> None:
    proc = build_subprocess_mock(
        stdout_chunks=[b"part-a", b"part-b"],
        stderr=b"warn: something",
    )
    received: list[str] = []

    outcome = await _run(proc, on_output=received.append)

    assert outcome.stdout == "part-apart-b"  # type: ignore[attr-defined]
    assert outcome.stderr == "warn: something"  # type: ignore[attr-defined]
    assert "".join(received) == outcome.stdout  # type: ignore[attr-defined]


async def test_on_output_none_is_unchanged_behaviour() -> None:
    proc = build_subprocess_mock(stdout_chunks=[b"abc", b"def"], stderr=b"err")

    outcome = await _run(proc, on_output=None)

    assert outcome.stdout == "abcdef"  # type: ignore[attr-defined]
    assert outcome.stderr == "err"  # type: ignore[attr-defined]
    assert outcome.exit_code == 0  # type: ignore[attr-defined]


async def test_single_chunk_stdout_equals_full() -> None:
    """The classic single-blob case (what most CLIs emit) still works."""
    proc = build_subprocess_mock(stdout=b'{"ok": true}\n')
    received: list[str] = []

    outcome = await _run(proc, on_output=received.append)

    assert outcome.stdout == '{"ok": true}\n'  # type: ignore[attr-defined]
    assert received == ['{"ok": true}\n']


async def test_on_output_raising_is_logged_and_swallowed() -> None:
    proc = build_subprocess_mock(stdout_chunks=[b"one", b"two"])

    def boom(_chunk: str) -> None:
        raise RuntimeError("flaky sink")

    with patch("dap_runtimes.adapters._cli_base.logger") as mock_logger:
        outcome = await _run(proc, on_output=boom)

    # The run still succeeds and returns the full stdout despite the sink raising.
    assert outcome.stdout == "onetwo"  # type: ignore[attr-defined]
    assert outcome.exit_code == 0  # type: ignore[attr-defined]
    # Each raising chunk is logged at warning level.
    assert mock_logger.warning.call_count >= 1


async def test_invalid_utf8_chunk_is_replaced_not_fatal() -> None:
    """A chunk that splits a multibyte sequence must not crash the decode."""
    proc = build_subprocess_mock(stdout_chunks=[b"\xff\xfe", b"tail"])
    received: list[str] = []

    outcome = await _run(proc, on_output=received.append)

    # errors="replace" — both the streamed chunks and the full stdout decode.
    assert received[0] == "��"
    assert outcome.stdout.endswith("tail")  # type: ignore[attr-defined]


async def test_timeout_still_kills_and_returns_timed_out() -> None:
    # ``hang=True`` makes stdout.read() block forever so wait_for fires.
    proc = build_subprocess_mock(hang=True)
    proc.returncode = None

    outcome = await _run(proc, on_output=None, timeout_seconds=0.05)

    assert outcome.timed_out is True  # type: ignore[attr-defined]
    assert outcome.stdout == ""  # type: ignore[attr-defined]
    # The subprocess was killed (single-process path uses .kill()).
    assert proc.kill.called or proc.terminate.called


async def test_timeout_with_on_output_still_kills() -> None:
    proc = build_subprocess_mock(hang=True)
    proc.returncode = None
    received: list[str] = []

    outcome = await _run(proc, on_output=received.append, timeout_seconds=0.05)

    assert outcome.timed_out is True  # type: ignore[attr-defined]
    assert received == []
