"""FastAPI dependencies — DB session, runtime registry."""

from __future__ import annotations

from collections.abc import Iterator

from dap_runtimes import RuntimeRegistry
from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker


def get_session(request: Request) -> Iterator[Session]:
    """Yield a transactional Session — commit on success, rollback on error."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_registry(request: Request) -> RuntimeRegistry:
    registry: RuntimeRegistry = request.app.state.runtime_registry
    return registry
