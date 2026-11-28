"""Tests for shared persistence helpers (#778 Phase 2).

``dap_engine.persistence.base`` consolidates the per-entity copies of
the ownership filter and the anti-enumeration get (four byte-identical
``_ownership_filter`` implementations before this change).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from dap_engine.persistence._common import NotFoundError
from dap_engine.persistence.base import get_owned_or_not_found, ownership_filter
from sqlalchemy import Uuid, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

OWNER = uuid.uuid4()
STRANGER = uuid.uuid4()


class _Base(DeclarativeBase):
    pass


class WidgetORM(_Base):
    """Minimal owned entity — mirrors the AgentORM/PipelineORM shape."""

    __tablename__ = "widgets"

    id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://", future=True)
    _Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all(
            [
                WidgetORM(id="own", user_id=OWNER),
                WidgetORM(id="foreign", user_id=STRANGER),
                WidgetORM(id="legacy", user_id=None),
            ]
        )
        s.commit()
        yield s
    engine.dispose()


# ---------------------------------------------------------------------------
# ownership_filter
# ---------------------------------------------------------------------------


def test_ownership_filter_admin_sees_all(session: Session) -> None:
    clauses = ownership_filter(WidgetORM, actor_id=OWNER, is_admin=True)
    assert clauses == []
    rows = session.scalars(select(WidgetORM).where(*clauses)).all()
    assert len(rows) == 3


def test_ownership_filter_non_admin_sees_only_own_rows(session: Session) -> None:
    clauses = ownership_filter(WidgetORM, actor_id=OWNER, is_admin=False)
    rows = session.scalars(select(WidgetORM).where(*clauses)).all()
    assert [r.id for r in rows] == ["own"]


# ---------------------------------------------------------------------------
# get_owned_or_not_found
# ---------------------------------------------------------------------------


def test_get_returns_own_row(session: Session) -> None:
    row = get_owned_or_not_found(
        session, WidgetORM, "own", actor_id=OWNER, is_admin=False, label="Widget"
    )
    assert row.id == "own"


def test_get_admin_reads_foreign_and_legacy_rows(session: Session) -> None:
    for entity_id in ("foreign", "legacy"):
        row = get_owned_or_not_found(
            session, WidgetORM, entity_id, actor_id=OWNER, is_admin=True, label="Widget"
        )
        assert row.id == entity_id


def test_get_missing_row_raises_not_found(session: Session) -> None:
    with pytest.raises(NotFoundError, match="Widget not found: nope"):
        get_owned_or_not_found(
            session, WidgetORM, "nope", actor_id=OWNER, is_admin=False, label="Widget"
        )


def test_get_foreign_row_raises_indistinguishable_not_found(session: Session) -> None:
    """Anti-enumeration: a foreign id must 404 with the SAME message as a
    missing id, so a non-admin can't probe for existence."""
    with pytest.raises(NotFoundError, match="Widget not found: foreign"):
        get_owned_or_not_found(
            session, WidgetORM, "foreign", actor_id=OWNER, is_admin=False, label="Widget"
        )
