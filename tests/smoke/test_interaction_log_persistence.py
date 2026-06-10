"""Persistence tests for the EU AI Act interaction log (#722).

Append-only record of redacted model interactions, separate from the
security ``audit_log``. Covers record/list roundtrip, filters,
pagination, and the retention purge.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from dap_engine.persistence.db import create_engine_for_sqlite, make_session_factory
from dap_engine.persistence.interaction_log import (
    list_interactions,
    purge_interactions,
    record_interaction,
)
from dap_engine.persistence.models import InteractionLogORM
from sqlalchemy.orm import Session

USER_A = uuid.uuid4()
USER_B = uuid.uuid4()


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine_for_sqlite(str(tmp_path / "state.db"))
    factory = make_session_factory(engine)
    with factory() as s:
        yield s
    engine.dispose()


def _record(session: Session, **overrides: object) -> None:
    defaults: dict[str, object] = {
        "user_id": USER_A,
        "surface": "assistant",
        "provider": "openai",
        "model": "gpt-test",
        "redacted_request": [{"role": "user", "content": "hello"}],
        "redacted_response": "hi there",
        "grounded": True,
    }
    defaults.update(overrides)
    record_interaction(session, **defaults)  # type: ignore[arg-type]


def test_record_and_list_roundtrip(session: Session) -> None:
    _record(session)
    session.commit()

    items, total = list_interactions(session, offset=0, limit=50)
    assert total == 1
    row = items[0]
    assert row.user_id == USER_A
    assert row.surface == "assistant"
    assert row.provider == "openai"
    assert row.model == "gpt-test"
    assert row.redacted_request == [{"role": "user", "content": "hello"}]
    assert row.redacted_response == "hi there"
    assert row.grounded is True
    assert row.created_at is not None


def test_list_is_newest_first_and_paginated(session: Session) -> None:
    for i in range(5):
        _record(session, redacted_response=f"reply-{i}")
    session.commit()

    items, total = list_interactions(session, offset=0, limit=2)
    assert total == 5
    assert len(items) == 2
    assert items[0].redacted_response == "reply-4"

    items, _ = list_interactions(session, offset=4, limit=2)
    assert [r.redacted_response for r in items] == ["reply-0"]


def test_list_filters_by_surface_and_user(session: Session) -> None:
    _record(session, surface="assistant", user_id=USER_A)
    _record(session, surface="run", user_id=USER_B)
    session.commit()

    items, total = list_interactions(session, offset=0, limit=50, surface="run")
    assert total == 1
    assert items[0].user_id == USER_B

    items, total = list_interactions(session, offset=0, limit=50, user_id=USER_A)
    assert total == 1
    assert items[0].surface == "assistant"


def test_purge_removes_only_rows_older_than_cutoff(session: Session) -> None:
    _record(session, redacted_response="old")
    _record(session, redacted_response="fresh")
    session.commit()

    # Backdate the first row past the cutoff.
    old_row = (
        session.query(InteractionLogORM).filter(InteractionLogORM.redacted_response == "old").one()
    )
    old_row.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=100)
    session.commit()

    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
    purged = purge_interactions(session, older_than=cutoff)
    session.commit()

    assert purged == 1
    items, total = list_interactions(session, offset=0, limit=50)
    assert total == 1
    assert items[0].redacted_response == "fresh"
