from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dap_runtimes import create_default_registry
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from dap_engine.api.agents import router as agents_router
from dap_engine.api.health import router as health_router
from dap_engine.api.pipelines import router as pipelines_router
from dap_engine.api.projects import router as projects_router
from dap_engine.api.runs import router as runs_router
from dap_engine.api.runtimes import router as runtimes_router
from dap_engine.api.settings import router as settings_router
from dap_engine.execution import RunRegistry
from dap_engine.persistence import repository as repo
from dap_engine.persistence.db import (
    create_engine_for_postgresql,
    create_engine_for_sqlite,
    detect_dialect,
    make_session_factory,
    pg_conn_string,
)

logger = logging.getLogger("dap.engine")


# Pool sizing for AsyncPostgresSaver. Conservative defaults — bump max_size if
# checkpoint contention shows up under load. Each background run holds a
# connection only for the duration of a checkpoint read/write, so 10 concurrent
# slots is enough for ~10 simultaneously-checkpointing pipelines without
# saturating Postgres connection limits. (#187)
_PG_POOL_MIN_SIZE = 2
_PG_POOL_MAX_SIZE = 10


@asynccontextmanager
async def _pg_pooled_checkpointer(conn_string: str) -> AsyncIterator[AsyncPostgresSaver]:
    """Yield an AsyncPostgresSaver backed by a psycopg AsyncConnectionPool.

    Replaces ``AsyncPostgresSaver.from_conn_string`` which opens a single
    AsyncConnection — concurrent runs serialize their checkpoint reads/writes
    through that one TCP socket. With a pool, checkpoint ops can run in
    parallel up to ``_PG_POOL_MAX_SIZE``. (#187)

    The connection kwargs (autocommit / prepare_threshold / row_factory) mirror
    what ``from_conn_string`` configures so AsyncPostgresSaver sees the same
    DBAPI behavior whether it's holding one connection or borrowing from a pool.
    """
    # Annotate as AsyncConnectionPool[AsyncConnection[DictRow]] so
    # AsyncPostgresSaver (which expects DictRow connections) typechecks; the
    # row_factory=dict_row in kwargs makes this true at runtime.
    pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
        conninfo=conn_string,
        min_size=_PG_POOL_MIN_SIZE,
        max_size=_PG_POOL_MAX_SIZE,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        # Defer opening to the async-context-manager entry; avoids the
        # "implicit pool open in __init__" deprecation warning.
        open=False,
    )
    async with pool:
        yield AsyncPostgresSaver(conn=pool)


@dataclass
class EngineConfig:
    db_path: str = "./.dap/state.db"
    # When set, takes precedence over db_path.  Prefix determines dialect:
    #   sqlite://…        → SQLite (same as db_path)
    #   postgresql+asyncpg://…  → PostgreSQL
    database_url: str | None = None
    host: str = "127.0.0.1"
    port: int = 7333
    # Hard cap for ``POST /agents/dry-run`` (#103). Each invocation pays
    # real LLM tokens, so we refuse calls whose agent ``budget_limit_usd``
    # (the top-level field on Agent / AgentDryRunDraft, not anything inside
    # ``runtime_config``) exceeds this. Belt-and-suspenders against a runaway
    # form value or a forgotten zero default in the UI.
    dry_run_budget_usd: float = 0.50


def create_app(config: EngineConfig | None = None) -> FastAPI:  # noqa: PLR0915
    cfg = config or EngineConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Determine dialect from DAP_DATABASE_URL or fall back to SQLite path.
        db_url = cfg.database_url
        dialect = detect_dialect(db_url) if db_url else "sqlite"

        checkpointer_ctx: Any
        if dialect == "postgresql":
            assert db_url is not None
            engine = create_engine_for_postgresql(db_url)
            # Pooled AsyncPostgresSaver — concurrent checkpoint ops parallelize
            # across up to _PG_POOL_MAX_SIZE psycopg connections instead of
            # serializing through a single TCP socket. (#187)
            checkpointer_ctx = _pg_pooled_checkpointer(pg_conn_string(db_url))
            db_label = db_url.split("@", 1)[-1] if "@" in db_url else db_url
        else:
            engine = create_engine_for_sqlite(cfg.db_path)
            checkpoint_path = Path(cfg.db_path).with_suffix(".checkpoints.db")
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpointer_ctx = AsyncSqliteSaver.from_conn_string(str(checkpoint_path))
            db_label = str(Path(cfg.db_path).resolve())

        session_factory = make_session_factory(engine)
        registry = create_default_registry()
        run_registry = RunRegistry()

        # Recover stale runs left by previous crashes
        with session_factory() as cleanup_session:
            stale_count = repo.mark_stale_running_runs_as_failed(
                cleanup_session,
                reason="engine restarted before run completed",
            )
            cleanup_session.commit()
        if stale_count > 0:
            logger.warning("marked %d stale running run(s) as failed", stale_count)

        async with AsyncExitStack() as stack:
            checkpointer = await stack.enter_async_context(checkpointer_ctx)
            # Create checkpointer tables (checkpoints, checkpoint_blobs, etc.) on
            # first start against a fresh DB. setup() is idempotent — no-op when
            # tables already exist. Required for both SQLite and PostgreSQL backends.
            await checkpointer.setup()

            app.state.config = cfg
            app.state.db_engine = engine
            app.state.db_dialect = dialect
            app.state.session_factory = session_factory
            app.state.runtime_registry = registry
            app.state.run_registry = run_registry
            app.state.checkpointer = checkpointer

            logger.info("dap-engine started — dialect=%s db=%s", dialect, db_label)
            try:
                yield
            finally:
                cancelled = await run_registry.shutdown(timeout=5.0)
                if cancelled:
                    logger.info("aborted %d running run(s) on shutdown", len(cancelled))
                    with session_factory() as shutdown_session:
                        for run_id in cancelled:
                            with contextlib.suppress(repo.NotFoundError):
                                repo.finalize_run(
                                    shutdown_session,
                                    run_id,
                                    final_status="aborted",
                                )
                        shutdown_session.commit()

                engine.dispose()
                logger.info("dap-engine stopped")

    app = FastAPI(
        title="dap-engine",
        version="0.0.1",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:7332",
            "http://127.0.0.1:7332",
        ],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )

    app.include_router(health_router)
    app.include_router(runtimes_router)
    app.include_router(agents_router)
    app.include_router(pipelines_router)
    app.include_router(projects_router)
    app.include_router(runs_router)
    app.include_router(settings_router)

    return app
