"""SQLite (WAL mode) + SQLAlchemy 2.0 engine factory."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from dap_engine.persistence.models import Base


def _enable_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def create_engine_for_sqlite(db_path: str) -> Engine:
    absolute = Path(db_path).resolve()
    absolute.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{absolute}",
        echo=False,
        future=True,
    )
    event.listen(engine, "connect", _enable_sqlite_pragmas)

    Base.metadata.create_all(engine)

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
