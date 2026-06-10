"""Unit tests for dap_database engine + session factories (#778 Phase 1).

The raw engine factories here create **no schema** — running migrations
is the consumer's job (``dap_engine.persistence.db`` keeps that part).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dap_database import (
    create_async_engine_for_url,
    create_postgresql_engine,
    create_sqlite_engine,
    make_async_session_factory,
    make_session_factory,
    session_scope,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

# ---------------------------------------------------------------------------
# SQLite engine
# ---------------------------------------------------------------------------


def test_create_sqlite_engine_creates_parent_dirs(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "dir" / "state.db"
    engine = create_sqlite_engine(str(db_path))
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        assert db_path.parent.is_dir()
    finally:
        engine.dispose()


def test_create_sqlite_engine_enables_wal_and_pragmas(tmp_path: Path) -> None:
    engine = create_sqlite_engine(str(tmp_path / "state.db"))
    try:
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
            assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000
    finally:
        engine.dispose()


def test_create_sqlite_engine_does_not_create_schema(tmp_path: Path) -> None:
    """Raw factory must not bake in any app schema — that's the consumer's job."""
    engine = create_sqlite_engine(str(tmp_path / "state.db"))
    try:
        with engine.connect() as conn:
            tables = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        assert tables == []
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# PostgreSQL engine (no live server — engine creation does not connect)
# ---------------------------------------------------------------------------


def test_create_postgresql_engine_rewrites_to_psycopg_driver() -> None:
    engine = create_postgresql_engine("postgresql+asyncpg://u:p@localhost:1/db")
    try:
        assert engine.url.drivername == "postgresql+psycopg"
    finally:
        engine.dispose()


def test_create_postgresql_engine_pool_defaults() -> None:
    engine = create_postgresql_engine("postgres://u:p@localhost:1/db")
    try:
        assert engine.pool.size() == 5
        assert engine.pool._max_overflow == 10  # type: ignore[attr-defined]
    finally:
        engine.dispose()


def test_create_postgresql_engine_pool_overrides() -> None:
    engine = create_postgresql_engine(
        "postgresql://u:p@localhost:1/db", pool_size=7, max_overflow=3
    )
    try:
        assert engine.pool.size() == 7
        assert engine.pool._max_overflow == 3  # type: ignore[attr-defined]
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Session factories
# ---------------------------------------------------------------------------


def test_session_scope_commits_on_success(tmp_path: Path) -> None:
    engine = create_sqlite_engine(str(tmp_path / "state.db"))
    try:
        factory = make_session_factory(engine)
        with session_scope(factory) as session:
            session.execute(text("CREATE TABLE t (x INTEGER)"))
            session.execute(text("INSERT INTO t VALUES (42)"))
        with session_scope(factory) as session:
            assert session.execute(text("SELECT x FROM t")).scalar() == 42
    finally:
        engine.dispose()


def test_session_scope_rolls_back_on_error(tmp_path: Path) -> None:
    engine = create_sqlite_engine(str(tmp_path / "state.db"))
    try:
        factory = make_session_factory(engine)
        with session_scope(factory) as session:
            session.execute(text("CREATE TABLE t (x INTEGER)"))
        with pytest.raises(RuntimeError), session_scope(factory) as session:
            session.execute(text("INSERT INTO t VALUES (1)"))
            raise RuntimeError("boom")
        with session_scope(factory) as session:
            assert session.execute(text("SELECT COUNT(*) FROM t")).scalar() == 0
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Async engine
# ---------------------------------------------------------------------------


def test_create_async_engine_for_url_sqlite(tmp_path: Path) -> None:
    engine = create_async_engine_for_url(None, str(tmp_path / "state.db"))
    assert isinstance(engine, AsyncEngine)
    assert engine.url.drivername == "sqlite+aiosqlite"


def test_create_async_engine_for_url_postgres() -> None:
    engine = create_async_engine_for_url("postgresql+asyncpg://u:p@localhost:1/db", None)
    assert engine.url.drivername == "postgresql+psycopg"


def test_make_async_session_factory_shape(tmp_path: Path) -> None:
    engine = create_async_engine_for_url(None, str(tmp_path / "state.db"))
    factory = make_async_session_factory(engine)
    assert isinstance(factory, async_sessionmaker)
