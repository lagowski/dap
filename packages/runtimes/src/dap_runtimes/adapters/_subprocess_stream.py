"""Shared deadlock-safe subprocess output drain with incremental streaming (#662).

Both the CLI-tool base (``_cli_base._run_subprocess``) and the ``bash`` adapter
spawn a subprocess and need to capture its full stdout/stderr *and* forward each
stdout fragment to an optional ``on_output`` sink as the process produces it.
The drain mechanics are identical and subtle enough to be worth sharing:

- stdout and stderr are drained **concurrently** — a full stderr pipe would
  deadlock a stdout-only reader;
- stdout is read in fixed chunks (``read(4096)``, *not* ``readline``: the
  StreamReader ``readline`` path deadlocks once a single line exceeds its
  64 KiB limit, which CLI stream-json output routinely does);
- each chunk is decoded with an *incremental* UTF-8 decoder so a multi-byte
  character split across two read boundaries is reassembled rather than emitted
  as replacement chars; the decoder is flushed (``decode(b"", final=True)``)
  after EOF;
- the full stdout is still decoded as a whole from the accumulated bytes by the
  caller, so the streamed fragments and the captured output always agree;
- a raising ``on_output`` sink is logged and swallowed — a flaky output consumer
  must never crash the read loop or fail the node;
- an optional ``stdin`` payload is written + closed concurrently (CLI adapters
  pipe the prompt through stdin; bash does not).

The caller owns the timeout / cancellation / process-reap policy: it wraps
:func:`drain_subprocess_output` in ``asyncio.wait_for(..., timeout)`` and tears
the process tree down on ``TimeoutError`` / ``CancelledError``. This keeps the
kill semantics (POSIX session-group SIGKILL) where they already live.
"""

from __future__ import annotations

import asyncio
import codecs
import logging
from typing import Final

from dap_types import OutputCallback

logger = logging.getLogger("dap.runtimes.subprocess_stream")

# Chunked stdout read size (#662). A plain ``read(n)`` is deliberate: the
# StreamReader ``readline`` path raises / deadlocks once a single line exceeds
# its 64 KiB limit, which CLI stream-json output routinely does.
STDOUT_READ_SIZE: Final = 4096


def emit_output(on_output: OutputCallback, text: str) -> None:
    """Forward one decoded stdout fragment to ``on_output``, swallowing errors.

    ``text`` is already incrementally decoded by the caller (so multi-byte
    characters split across read boundaries stay intact). A misbehaving sink
    (e.g. a DB write that transiently fails) must never crash the subprocess
    read loop or fail the node — log and continue.
    """
    try:
        on_output(text)
    except Exception:
        logger.warning("on_output callback raised; dropping stdout chunk", exc_info=True)


async def drain_subprocess_output(
    process: asyncio.subprocess.Process,
    *,
    on_output: OutputCallback | None = None,
    stdin: bytes | None = None,
) -> tuple[bytes, bytes]:
    """Concurrently drain stdout + stderr (and optionally feed stdin).

    Returns ``(stdout_bytes, stderr_bytes)`` — the complete captured pipes,
    independent of ``on_output``. While draining stdout, each decoded fragment
    is forwarded to ``on_output`` (when set). ``on_output`` never affects the
    returned bytes.

    The caller is responsible for wrapping this in ``asyncio.wait_for`` for the
    timeout budget and for reaping the process (``await process.wait()``) once
    this returns.
    """

    async def _feed_stdin() -> None:
        if stdin is None or process.stdin is None:
            return
        # ``communicate`` swallows broken-pipe / reset errors when the child
        # exits before consuming all of stdin; mirror that so a fast-exiting
        # subprocess doesn't turn into a spurious failure.
        try:
            process.stdin.write(stdin)
            await process.stdin.drain()
            process.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            return

    async def _read_stdout() -> bytes:
        buf = bytearray()
        assert process.stdout is not None
        # Incremental UTF-8 decode so a multi-byte char split across two read()
        # boundaries isn't emitted as replacement chars. The returned full
        # stdout is still decoded as a whole from ``buf`` by the caller, so the
        # callback stream and the captured output agree.
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            chunk = await process.stdout.read(STDOUT_READ_SIZE)
            if not chunk:
                break
            buf += chunk
            if on_output is not None:
                text = decoder.decode(chunk)
                if text:
                    emit_output(on_output, text)
        if on_output is not None:
            tail = decoder.decode(b"", final=True)
            if tail:
                emit_output(on_output, tail)
        return bytes(buf)

    async def _read_stderr() -> bytes:
        assert process.stderr is not None
        return await process.stderr.read()

    stdout_bytes, stderr_bytes, _ = await asyncio.gather(
        _read_stdout(), _read_stderr(), _feed_stdin()
    )
    return stdout_bytes, stderr_bytes


__all__ = ["STDOUT_READ_SIZE", "drain_subprocess_output", "emit_output"]
