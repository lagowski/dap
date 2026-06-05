"""Server-Sent Events (SSE) stream of run-execution events (#662, Phase 3a).

``GET /runs/{run_id}/events`` pushes run-execution events to a connected
client in real time, replacing the need to poll ``GET /runs/{id}`` every
1-2s. For Phase 3a the events are *derived* from the existing run +
node-execution-log state via server-side polling — there are no
executor/adapter changes; the executor still writes the same rows, and
this endpoint diffs successive reads of them.

Event schema (stable contract — consumed by the dashboard in 3c and the
CLI ``--follow`` in 3d). Every event is an SSE frame::

    event: <name>
    data: <flat JSON object>

Events:

* ``snapshot`` — emitted once at stream open with the full current run
  state: ``{run_id, final_status, current_node, node_statuses,
  ended_at}``. Lets a late-connecting client render the current state
  without a separate ``GET /runs/{id}``.
* ``run_status`` — emitted when ``final_status`` or ``current_node``
  changes: ``{run_id, final_status, current_node}``.
* ``node_started`` — emitted when a node first appears as ``running``:
  ``{run_id, node_id}``.
* ``node_finished`` — emitted when a node transitions to
  ``success|failed|skipped``: ``{run_id, node_id, status, duration_ms,
  tokens_used, cost_usd, ended_at}`` (metrics from the node log if
  available; zeroed / ``None`` when the log row isn't written yet).
* ``node_log`` — emitted for each incremental output chunk a node
  produces (#662, Phase 3b): ``{run_id, node_id, seq, content, stream}``
  where ``seq`` is the chunk's monotonic id and ``stream`` is
  ``stdout``/``stderr``. A connecting client receives only chunks
  produced *after* connect — the stream does **not** replay the full
  stdout history (the ``snapshot`` reflects current state, not the byte
  log). On the terminal tick, all remaining chunks are drained *before*
  ``run_finished`` so trailing output is never dropped. Until Phase 3b-2
  wires adapters to append chunks, real runs emit no ``node_log`` events.
* ``run_finished`` — emitted once when the run reaches a terminal
  ``final_status`` (``success|failed|aborted``); the stream then closes:
  ``{run_id, final_status, ended_at}``.

Non-blocking guarantees (respects #621/#650/#636):

* The endpoint is ``async``; every DB read runs in a worker thread via
  ``run_in_threadpool`` so the event loop is never blocked by sync
  SQLAlchemy.
* A *fresh* ``Session`` is opened from the factory for each poll tick and
  closed immediately — no session is held open across the stream (read
  isolation, #636).
* The stream terminates cleanly on a terminal status (final event then
  return) and on client disconnect (``request.is_disconnected()``).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import anyio
from dap_types import NodeExecutionLog, NodeOutputChunk, Run
from fastapi import Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from dap_engine.api.deps import get_session_factory
from dap_engine.auth.users import current_active_user
from dap_engine.persistence import repository as repo
from dap_engine.persistence.models import UserORM

logger = logging.getLogger("dap.engine.api.run_events")

# Terminal final_status values — a run in one of these has finished
# executing; ``paused`` is intentionally NOT terminal so the client sees
# the gate and the subsequent resume on the same stream.
_TERMINAL_STATUSES = frozenset({"success", "failed", "aborted"})

# Node statuses that count as "finished" for a node_finished event.
_NODE_FINISHED_STATUSES = frozenset({"success", "failed", "skipped"})

# Poll cadence for the server-side derivation loop. A module constant so
# tests / config can reference it; ~1s keeps latency low without hammering
# the DB.
POLL_INTERVAL = 1.0

# Keep-alive comment cadence (seconds) — emitted as an SSE comment line so
# intermediary proxies don't drop an otherwise-idle connection.
KEEPALIVE_INTERVAL = 15.0


@dataclass(frozen=True)
class RunSnapshot:
    """Immutable view of the run state used to diff successive poll reads.

    Built from a :class:`~dap_types.Run` plus its node-execution logs. Kept
    deliberately small and flat so :func:`_diff_events` is a pure function
    over plain data (the unit-test surface).
    """

    run_id: str
    final_status: str
    current_node: str | None
    ended_at: str | None
    node_statuses: dict[str, str] = field(default_factory=dict)
    # node_id -> its execution log, for metrics on node_finished events.
    node_logs: dict[str, NodeExecutionLog] = field(default_factory=dict)


def _snapshot_from_run(run: Run, node_logs: list[NodeExecutionLog]) -> RunSnapshot:
    """Build a :class:`RunSnapshot` from a run row and its node logs."""
    return RunSnapshot(
        run_id=run.id,
        final_status=run.final_status,
        current_node=run.current_node,
        ended_at=run.ended_at.isoformat() if run.ended_at is not None else None,
        node_statuses=dict(run.node_statuses),
        node_logs={log.node_id: log for log in node_logs},
    )


def _sse(event: str, data: dict[str, object]) -> str:
    """Format one SSE frame: ``event:`` + ``data:`` (JSON) + blank line."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# Type alias for a (event_name, data) pair before serialisation.
_Event = tuple[str, dict[str, object]]


def _node_finished_payload(
    snapshot: RunSnapshot, node_id: str, node_status: str
) -> dict[str, object]:
    """Build the node_finished payload, pulling metrics from the node log."""
    log = snapshot.node_logs.get(node_id)
    return {
        "run_id": snapshot.run_id,
        "node_id": node_id,
        "status": node_status,
        "duration_ms": log.duration_ms if log is not None else 0,
        "tokens_used": log.tokens_used if log is not None else 0,
        "cost_usd": log.cost_usd if log is not None else 0.0,
        "ended_at": (
            log.ended_at.isoformat() if log is not None and log.ended_at is not None else None
        ),
    }


def _diff_events(prev: RunSnapshot | None, cur: RunSnapshot) -> list[_Event]:
    """Derive the ordered list of SSE events between two snapshots.

    Pure function — no I/O. This is the unit-tested core of the endpoint.

    * ``prev is None`` (stream open): emit ``snapshot`` with the full
      current state, followed by ``run_finished`` if the run is already
      terminal (so a client connecting to an already-finished run still
      sees the terminal marker and the stream closes).
    * Otherwise diff: ``run_status`` on final_status/current_node change,
      ``node_started`` for nodes newly ``running``, ``node_finished`` for
      nodes newly in a finished status (with metrics), and a trailing
      ``run_finished`` when the run becomes terminal.
    """
    events: list[_Event] = []

    if prev is None:
        events.append(
            (
                "snapshot",
                {
                    "run_id": cur.run_id,
                    "final_status": cur.final_status,
                    "current_node": cur.current_node,
                    "node_statuses": dict(cur.node_statuses),
                    "ended_at": cur.ended_at,
                },
            )
        )
        if cur.final_status in _TERMINAL_STATUSES:
            events.append(_run_finished_event(cur))
        return events

    # run_status: final_status or current_node changed.
    if prev.final_status != cur.final_status or prev.current_node != cur.current_node:
        events.append(
            (
                "run_status",
                {
                    "run_id": cur.run_id,
                    "final_status": cur.final_status,
                    "current_node": cur.current_node,
                },
            )
        )

    # Per-node transitions.
    for node_id, node_status in cur.node_statuses.items():
        prev_status = prev.node_statuses.get(node_id)
        if node_status == prev_status:
            continue
        if node_status == "running":
            events.append(("node_started", {"run_id": cur.run_id, "node_id": node_id}))
        elif node_status in _NODE_FINISHED_STATUSES:
            events.append(("node_finished", _node_finished_payload(cur, node_id, node_status)))

    # run_finished last — a terminal run closes the stream after this.
    if cur.final_status in _TERMINAL_STATUSES and prev.final_status not in _TERMINAL_STATUSES:
        events.append(_run_finished_event(cur))

    return events


def _run_finished_event(snapshot: RunSnapshot) -> _Event:
    return (
        "run_finished",
        {
            "run_id": snapshot.run_id,
            "final_status": snapshot.final_status,
            "ended_at": snapshot.ended_at,
        },
    )


def _read_snapshot(
    session_factory: sessionmaker[Session],
    run_id: str,
    *,
    actor_id: uuid.UUID,
    is_admin: bool,
) -> RunSnapshot:
    """Read a fresh snapshot in a brand-new session (sync — run in threadpool).

    A new ``Session`` is opened and closed per call so no session is held
    open across the stream (read isolation, #636). Raises
    ``repo.NotFoundError`` for a missing / cross-user run.
    """
    with session_factory() as session:
        run = repo.get_run(session, run_id, actor_id=actor_id, is_admin=is_admin)
        node_logs = repo.list_run_node_logs(
            session,
            run_id,
            actor_id=actor_id,
            is_admin=is_admin,
        )
        return _snapshot_from_run(run, node_logs)


def _node_log_event(chunk: NodeOutputChunk) -> _Event:
    """Build a ``node_log`` SSE event from an output chunk.

    ``seq`` is the chunk's monotonic id — the cursor the stream advances on.
    """
    return (
        "node_log",
        {
            "run_id": chunk.run_id,
            "node_id": chunk.node_id,
            "seq": chunk.id,
            "content": chunk.content,
            "stream": chunk.stream,
        },
    )


def _read_latest_chunk_id(session_factory: sessionmaker[Session], run_id: str) -> int:
    """Read the current max output-chunk id in a fresh session (run in threadpool).

    Used once at connect to seed the cursor so the client streams only
    chunks produced *after* connect (no replay of history).
    """
    with session_factory() as session:
        return repo.latest_output_chunk_id(session, run_id)


def _read_chunks_since(
    session_factory: sessionmaker[Session], run_id: str, *, after_id: int
) -> list[NodeOutputChunk]:
    """Read output chunks with ``id > after_id`` in a fresh session (threadpool)."""
    with session_factory() as session:
        return repo.list_output_chunks_since(session, run_id, after_id=after_id)


async def stream_run_events(
    run_id: str,
    request: Request,
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    user: UserORM = Depends(current_active_user),
) -> StreamingResponse:
    """Stream run-execution events as Server-Sent Events.

    See the module docstring for the full event schema. The first read is
    performed *before* the stream opens so a missing / unauthorized run
    yields a clean ``404`` rather than an empty ``200`` stream.
    """
    try:
        first = await run_in_threadpool(
            _read_snapshot,
            session_factory,
            run_id,
            actor_id=user.id,
            is_admin=user.is_superuser,
        )
        # Connect cursor: a connecting client streams only chunks produced
        # *after* connect, so seed from the current max chunk id. The
        # snapshot above carries current state; the byte log is not replayed.
        last_chunk_id = await run_in_threadpool(
            _read_latest_chunk_id, session_factory, run_id
        )
    except repo.NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    async def gen() -> AsyncIterator[str]:
        nonlocal last_chunk_id
        prev: RunSnapshot | None = None
        cur: RunSnapshot | None = first
        elapsed_since_keepalive = 0.0

        while True:
            if await request.is_disconnected():
                return

            if cur is None:
                # Re-read failed transiently (e.g. run deleted) — close.
                return

            # Drain any new output chunks BEFORE the snapshot diff so that
            # on a terminal tick trailing output is flushed ahead of the
            # ``run_finished`` frame (which closes the stream).
            chunks = await run_in_threadpool(
                _read_chunks_since, session_factory, run_id, after_id=last_chunk_id
            )
            for chunk in chunks:
                event_name, data = _node_log_event(chunk)
                yield _sse(event_name, data)
                last_chunk_id = chunk.id

            for event_name, data in _diff_events(prev, cur):
                yield _sse(event_name, data)
                if event_name == "run_finished":
                    return

            prev = cur

            if cur.final_status in _TERMINAL_STATUSES:
                # Defensive: _diff_events already emitted run_finished, but
                # guard against ever looping on a terminal run.
                return

            await anyio.sleep(POLL_INTERVAL)
            elapsed_since_keepalive += POLL_INTERVAL
            if elapsed_since_keepalive >= KEEPALIVE_INTERVAL:
                elapsed_since_keepalive = 0.0
                yield ": ping\n\n"

            try:
                cur = await run_in_threadpool(
                    _read_snapshot,
                    session_factory,
                    run_id,
                    actor_id=user.id,
                    is_admin=user.is_superuser,
                )
            except repo.NotFoundError:
                # Run vanished mid-stream — close cleanly.
                return

    return StreamingResponse(gen(), media_type="text/event-stream")
