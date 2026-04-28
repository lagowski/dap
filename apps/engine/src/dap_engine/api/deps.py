"""FastAPI dependencies — DB session, runtime registry, run registry."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from dap_runtimes import RuntimeRegistry
from fastapi import Request
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.orm import Session, sessionmaker

from dap_engine.execution import RunRegistry

if TYPE_CHECKING:
    # Runtime cycle: dap_engine.app imports the api routers (which
    # import this module). TYPE_CHECKING keeps the type information
    # without the import-time edge.
    from dap_engine.app import EngineConfig


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


def get_session_factory(request: Request) -> sessionmaker[Session]:
    """Return the session factory — used by background tasks that need a fresh session."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    return factory


def get_registry(request: Request) -> RuntimeRegistry:
    registry: RuntimeRegistry = request.app.state.runtime_registry
    return registry


def get_run_registry(request: Request) -> RunRegistry:
    run_registry: RunRegistry = request.app.state.run_registry
    return run_registry


def get_checkpointer(request: Request) -> BaseCheckpointSaver[Any]:
    checkpointer: BaseCheckpointSaver[Any] = request.app.state.checkpointer
    return checkpointer


def get_engine_config(request: Request) -> EngineConfig:
    """Return the active ``EngineConfig`` for endpoints that need its values."""
    config: EngineConfig = request.app.state.config
    return config
