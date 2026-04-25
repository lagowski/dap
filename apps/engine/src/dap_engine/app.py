from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from dap_runtimes import create_default_registry
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dap_engine.api.agents import router as agents_router
from dap_engine.api.health import router as health_router
from dap_engine.api.pipelines import router as pipelines_router
from dap_engine.api.runs import router as runs_router
from dap_engine.api.runtimes import router as runtimes_router
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

        app.state.config = cfg
        app.state.db_engine = engine
        app.state.session_factory = session_factory
        app.state.runtime_registry = registry

        logger.info("dap-engine started — db=%s", Path(cfg.db_path).resolve())
        try:
            yield
        finally:
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
