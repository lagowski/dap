"""Tests for node-output-chunk persistence (#662, Phase 3b-1).

The ``node_output_chunks`` table backs incremental node-stdout streaming
over the SSE endpoint. Adapters will append chunks in Phase 3b-2; this
phase builds + tests the table, the typed model, and the three repo
helpers in isolation by inserting chunks directly.

Repo helpers under test:

* ``append_output_chunk`` — insert one chunk; the returned
  :class:`~dap_types.NodeOutputChunk` carries a populated monotonic ``id``.
* ``list_output_chunks_since`` — page chunks with ``id > after_id`` in
  ascending id order (the incremental cursor read).
* ``latest_output_chunk_id`` — the max chunk id for a run (the SSE start
  cursor); ``0`` for a run with no chunks.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from dap_engine.persistence import repository as repo
from dap_engine.persistence.db import create_engine_for_sqlite, make_session_factory
from dap_engine.persistence.models import RunORM
from dap_types import NodeOutputChunk
from sqlalchemy.orm import Session, sessionmaker


def _seed_run(session_factory: sessionmaker[Session], *, run_id: str) -> None:
    now = datetime.now(UTC)
    initial_state: dict[str, Any] = {
        "run_id": run_id,
        "repo": "rafeekpro/test-repo",
        "branch": "main",
        "commit_sha": None,
        "available_issues": [],
        "selected_issue_ids": [],
        "tests_generated": False,
        "test_files": [],
        "test_generation_errors": [],
        "max_attempts": 3,
        "attempt": 0,
        "tests_passed": False,
        "last_test_output": "",
        "modified_files": [],
        "implementation_notes": None,
        "verification_status": "pending",
        "verification_reason": None,
        "final_status": "running",
        "extensions": {},
    }
    with session_factory() as session:
        session.add(
            RunORM(
                id=run_id,
                project_id=None,
                pipeline_id="pipe-1",
                pipeline_version=1,
                trigger_source="cli",
                initial_state=initial_state,
                current_node="implement",
                node_statuses={"implement": "running"},
                final_status="running",
                started_at=now,
                ended_at=None,
                tokens_used=0,
                cost_usd=0.0,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    tmp = tempfile.mkdtemp(prefix="dap-output-chunks-")
    engine = create_engine_for_sqlite(str(Path(tmp) / "state.db"))
    try:
        yield make_session_factory(engine)
    finally:
        engine.dispose()


def test_append_output_chunk_returns_populated_id(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = str(uuid.uuid4())
    _seed_run(session_factory, run_id=run_id)

    with session_factory() as session:
        chunk = repo.append_output_chunk(
            session,
            run_id=run_id,
            node_id="implement",
            content="hello world\n",
        )
        session.commit()

    assert isinstance(chunk, NodeOutputChunk)
    assert chunk.id is not None
    assert chunk.id > 0
    assert chunk.run_id == run_id
    assert chunk.node_id == "implement"
    assert chunk.content == "hello world\n"
    assert chunk.stream == "stdout"
    assert chunk.execution_id is None
    assert isinstance(chunk.created_at, datetime)


def test_append_output_chunk_accepts_execution_id_and_stream(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = str(uuid.uuid4())
    _seed_run(session_factory, run_id=run_id)

    with session_factory() as session:
        chunk = repo.append_output_chunk(
            session,
            run_id=run_id,
            node_id="implement",
            content="boom\n",
            execution_id="log-123",
            stream="stderr",
        )
        session.commit()

    assert chunk.execution_id == "log-123"
    assert chunk.stream == "stderr"


def test_append_output_chunk_ids_are_monotonic(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = str(uuid.uuid4())
    _seed_run(session_factory, run_id=run_id)

    with session_factory() as session:
        first = repo.append_output_chunk(
            session, run_id=run_id, node_id="implement", content="a"
        )
        second = repo.append_output_chunk(
            session, run_id=run_id, node_id="implement", content="b"
        )
        session.commit()

    assert second.id > first.id


def test_list_output_chunks_since_returns_only_newer_in_order(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = str(uuid.uuid4())
    _seed_run(session_factory, run_id=run_id)

    with session_factory() as session:
        ids = [
            repo.append_output_chunk(
                session, run_id=run_id, node_id="implement", content=str(i)
            ).id
            for i in range(5)
        ]
        session.commit()

    cutoff = ids[2]
    with session_factory() as session:
        rows = repo.list_output_chunks_since(session, run_id, after_id=cutoff)

    assert [r.id for r in rows] == [ids[3], ids[4]]
    assert [r.content for r in rows] == ["3", "4"]


def test_list_output_chunks_since_zero_returns_all(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = str(uuid.uuid4())
    _seed_run(session_factory, run_id=run_id)

    with session_factory() as session:
        for i in range(3):
            repo.append_output_chunk(
                session, run_id=run_id, node_id="implement", content=str(i)
            )
        session.commit()

    with session_factory() as session:
        rows = repo.list_output_chunks_since(session, run_id, after_id=0)
    assert [r.content for r in rows] == ["0", "1", "2"]


def test_list_output_chunks_since_respects_limit(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = str(uuid.uuid4())
    _seed_run(session_factory, run_id=run_id)

    with session_factory() as session:
        for i in range(10):
            repo.append_output_chunk(
                session, run_id=run_id, node_id="implement", content=str(i)
            )
        session.commit()

    with session_factory() as session:
        rows = repo.list_output_chunks_since(session, run_id, after_id=0, limit=4)
    assert len(rows) == 4
    assert [r.content for r in rows] == ["0", "1", "2", "3"]


def test_list_output_chunks_since_isolates_by_run(
    session_factory: sessionmaker[Session],
) -> None:
    run_a = str(uuid.uuid4())
    run_b = str(uuid.uuid4())
    _seed_run(session_factory, run_id=run_a)
    _seed_run(session_factory, run_id=run_b)

    with session_factory() as session:
        repo.append_output_chunk(session, run_id=run_a, node_id="n", content="a-chunk")
        repo.append_output_chunk(session, run_id=run_b, node_id="n", content="b-chunk")
        session.commit()

    with session_factory() as session:
        rows = repo.list_output_chunks_since(session, run_a, after_id=0)
    assert [r.content for r in rows] == ["a-chunk"]


def test_latest_output_chunk_id_is_zero_for_empty_run(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = str(uuid.uuid4())
    _seed_run(session_factory, run_id=run_id)
    with session_factory() as session:
        assert repo.latest_output_chunk_id(session, run_id) == 0


def test_latest_output_chunk_id_returns_max(
    session_factory: sessionmaker[Session],
) -> None:
    run_id = str(uuid.uuid4())
    _seed_run(session_factory, run_id=run_id)
    with session_factory() as session:
        last = 0
        for i in range(4):
            last = repo.append_output_chunk(
                session, run_id=run_id, node_id="n", content=str(i)
            ).id
        session.commit()
    with session_factory() as session:
        assert repo.latest_output_chunk_id(session, run_id) == last
