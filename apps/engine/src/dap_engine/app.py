from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from dap_runtimes import create_default_registry
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from dap_engine.api.agents import router as agents_router
from dap_engine.api.health import router as health_router
from dap_engine.api.pipelines import router as pipelines_router
from dap_engine.api.runs import router as runs_router
from dap_engine.api.runtimes import router as runtimes_router
from dap_engine.execution import RunRegistry
from dap_engine.persistence import repository as repo
from dap_engine.persistence.db import create_engine_for_sqlite, make_session_factory

logger = logging.getLogger("dap.engine")


@dataclass
class EngineConfig:
    db_path: str = "./.dap/state.db"
    host: str = "127.0.0.1"
    port: int = 7333


def create_app(config: EngineConfig | None = None) -> FastAPI:
    cfg = config or EngineConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine_for_sqlite(cfg.db_path)
        session_factory = make_session_factory(engine)
        registry = create_default_registry()
        run_registry = RunRegistry()

        # LangGraph checkpoints live in a sibling SQLite file so they don't
        # collide with the application schema (Alembic-managed).
        checkpoint_path = Path(cfg.db_path).with_suffix(".checkpoints.db")
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

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
            checkpointer = await stack.enter_async_context(
                AsyncSqliteSaver.from_conn_string(str(checkpoint_path))
            )

            app.state.config = cfg
            app.state.db_engine = engine
            app.state.session_factory = session_factory
            app.state.runtime_registry = registry
            app.state.run_registry = run_registry
            app.state.checkpointer = checkpointer

            logger.info(
                "dap-engine started — db=%s, checkpoints=%s",
                Path(cfg.db_path).resolve(),
                checkpoint_path.resolve(),
            )
            try:
                yield
            finally:
                # Graceful shutdown — abort all running tasks
                cancelled = await run_registry.shutdown(timeout=5.0)
                if cancelled:
                    logger.info("aborted %d running run(s) on shutdown", len(cancelled))
                    # Mark them as aborted in DB
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
        # Dashboard dev server (Next.js default :3000) and the legacy port
        # the CLI scaffolds (:7332) are both allowed so either way of
        # running the UI works without a CORS surprise.
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
    app.include_router(runs_router)

    return app
