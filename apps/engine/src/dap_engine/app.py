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
from dap_engine.auth import fastapi_users
from dap_engine.auth.api_token_routes import router as api_token_router
from dap_engine.auth.db import create_async_engine_for_url, make_async_session_factory
from dap_engine.auth.oauth import make_github_client, make_google_client
from dap_engine.auth.schemas import UserCreate, UserRead, UserUpdate
from dap_engine.auth.users import auth_backend, configure_jwt
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


@asynccontextmanager
async def _pg_pooled_checkpointer(
    conn_string: str,
    *,
    min_size: int,
    max_size: int,
) -> AsyncIterator[AsyncPostgresSaver]:
    """Yield an AsyncPostgresSaver backed by a psycopg AsyncConnectionPool.

    Replaces ``AsyncPostgresSaver.from_conn_string`` which opens a single
    persistent AsyncConnection. (#187)

    ``AsyncPostgresSaver._cursor()`` acquires a per-saver ``asyncio.Lock``
    before borrowing from the pool, so checkpoint reads/writes are serialised
    across all concurrent runs sharing this saver instance — only one
    connection is borrowed at a time. ``max_size`` therefore governs how many
    *spare* connections stay open, not actual checkpoint parallelism.
    ``min_size`` is the more important knob: it controls how many warm
    connections the pool keeps ready so a checkpoint write that arrives after a
    long idle phase (Phase 1 ~10 min) doesn't have to create a new connection
    through a potentially-stale k8s NodePort NAT. (#238)

    The connection kwargs (autocommit / prepare_threshold / row_factory) mirror
    what ``from_conn_string`` configures so AsyncPostgresSaver sees the same
    DBAPI behavior whether it's holding one connection or borrowing from a pool.
    """
    # Annotate as AsyncConnectionPool[AsyncConnection[DictRow]] so
    # AsyncPostgresSaver (which expects DictRow connections) typechecks; the
    # row_factory=dict_row in kwargs makes this true at runtime.
    pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
        conninfo=conn_string,
        min_size=min_size,
        max_size=max_size,
        # psycopg_pool default max_idle=600s (10 min) matches Cortex Phase 1
        # runtime exactly — the pool reaps idle connections just as the gate
        # checkpoint write arrives, causing PoolTimeout (#238).  Set to 1 h
        # so connections survive the full pipeline (~40 min).
        max_idle=3600.0,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            # TCP keepalives so k8s NodePort NAT doesn't silently drop
            # idle connections (causes "server closed connection unexpectedly"
            # when the first checkpoint read hits a dead socket).
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
        # Defer opening to the async-context-manager entry; avoids the
        # "implicit pool open in __init__" deprecation warning.
        open=False,
    )
    async with pool:
        yield AsyncPostgresSaver(conn=pool)


# Local-dev defaults: dashboard on :3000, alt port :7332. Override at
# deploy time via ``EngineConfig.cors_origins`` or the ``DAP_CORS_ORIGINS``
# env var (#259).
DEFAULT_CORS_ORIGINS: list[str] = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:7332",
    "http://127.0.0.1:7332",
]


def parse_cors_origins(raw: str | None) -> list[str] | None:
    """Parse ``DAP_CORS_ORIGINS`` (comma-separated origins) into a list.

    Returns ``None`` when the env var is unset or empty after stripping —
    callers treat that as "use the default local-dev list". Whitespace
    around individual entries is trimmed; empty entries are dropped so a
    trailing comma doesn't produce a bogus origin string.
    """
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",")]
    cleaned = [p for p in parts if p]
    return cleaned or None


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
    # PostgreSQL checkpointer pool sizing (#187, #238).
    # AsyncPostgresSaver serialises checkpoint ops behind a Lock, so only 1
    # connection is in flight at a time — max_size governs burst concurrency
    # across simultaneous runs, not within one.  min_size=4 matches the
    # psycopg_pool default and keeps enough warm connections for the gate
    # checkpoint write that arrives after a long idle Phase 1.
    pg_pool_min_size: int = 4
    pg_pool_max_size: int = 10
    # CORS origins permitted on the engine's REST API (#259). ``None``
    # means "fall back to ``DEFAULT_CORS_ORIGINS``" — the local-dev list.
    # Production deployments override via env var ``DAP_CORS_ORIGINS``
    # (parsed in ``__main__``) or by passing ``cors_origins=[...]`` here.
    cors_origins: list[str] | None = None
    # Auth (v0.3, see #299).
    # ``auth_jwt_secret``: required for any auth-protected route.  Tests
    # set a deterministic value; production reads ``DAP_AUTH_JWT_SECRET``
    # in ``__main__`` and refuses to start with a default. We accept None
    # here only to keep the dataclass default-constructible; the lifespan
    # generates a per-process random secret in that case (sufficient for
    # local dev where every restart invalidates outstanding tokens).
    auth_jwt_secret: str | None = None
    auth_access_ttl_seconds: int = 60 * 15
    # OAuth (v0.3, sub-A2). Each provider is opt-in: when both
    # client_id and client_secret are set the corresponding /auth/<provider>
    # router is mounted; otherwise nothing is exposed for that provider.
    # Self-host installs can run with email+password only and add OAuth
    # later by setting these env vars and restarting.
    oauth_github_client_id: str | None = None
    oauth_github_client_secret: str | None = None
    oauth_google_client_id: str | None = None
    oauth_google_client_secret: str | None = None


def _setup_auth(cfg: EngineConfig) -> tuple[Any, Any, str]:
    """Build the JWT-config + async engine + session factory for fastapi-users.

    Returns ``(async_engine, async_session_factory, jwt_secret)``. The
    async engine is owned by the caller, which must dispose it on shutdown.
    ``jwt_secret`` is returned so OAuth routers can reuse it as their
    state-secret (CSRF protection of the OAuth flow); a separate secret
    would force operators to manage two env vars without a security gain.

    A ``None`` ``auth_jwt_secret`` is replaced with a per-process random,
    which means outstanding tokens become invalid on the next restart.
    That's the safer default for local dev / tests; production must set
    ``DAP_AUTH_JWT_SECRET`` explicitly.
    """
    import secrets  # noqa: PLC0415 — local to keep top of file uncluttered

    jwt_secret = cfg.auth_jwt_secret or secrets.token_urlsafe(32)
    configure_jwt(jwt_secret, cfg.auth_access_ttl_seconds)
    async_engine = create_async_engine_for_url(cfg.database_url, cfg.db_path)
    async_session_factory = make_async_session_factory(async_engine)
    return async_engine, async_session_factory, jwt_secret


def create_app(config: EngineConfig | None = None) -> FastAPI:  # noqa: PLR0915
    cfg = config or EngineConfig()

    # Auth setup runs synchronously at app-construction time so the OAuth
    # routers (mounted below at module scope, not in lifespan) have access
    # to the same JWT/state secret. ``create_async_engine`` doesn't open
    # connections until the first request, so this is safe to do here.
    auth_async_engine, async_session_factory, oauth_state_secret = _setup_auth(cfg)

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
            # across up to cfg.pg_pool_max_size psycopg connections instead of
            # serializing through a single TCP socket. (#187)
            checkpointer_ctx = _pg_pooled_checkpointer(
                pg_conn_string(db_url),
                min_size=cfg.pg_pool_min_size,
                max_size=cfg.pg_pool_max_size,
            )
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
            app.state.async_session_factory = async_session_factory
            app.state.auth_async_engine = auth_async_engine

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

                await auth_async_engine.dispose()
                engine.dispose()
                logger.info("dap-engine stopped")

    app = FastAPI(
        title="dap-engine",
        version="0.0.1",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins if cfg.cors_origins is not None else DEFAULT_CORS_ORIGINS,
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

    # Auth routes (v0.3, see #299).
    # Existing endpoints are NOT yet auth-protected — that lands in a
    # follow-up PR alongside the user_id ownership columns. This PR only
    # exposes the auth surface so dashboards / CLIs can register and log
    # in; the full auth-and-ownership migration ships separately.
    app.include_router(
        fastapi_users.get_auth_router(auth_backend),
        prefix="/auth/jwt",
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate),
        prefix="/auth",
        tags=["auth"],
    )
    app.include_router(
        fastapi_users.get_users_router(UserRead, UserUpdate),
        prefix="/users",
        tags=["users"],
    )

    # API tokens (CLI / scripts) — JWT-only management surface; the
    # tokens themselves authenticate any other route via the api-token
    # backend wired into ``fastapi_users``.
    app.include_router(api_token_router)

    # OAuth routers — only mounted when both credentials are present.
    # ``associate_by_email=True`` lets a user with an existing local
    # password account link a GitHub/Google identity by logging in with
    # the same email. ``is_verified_by_default=True`` flags OAuth-created
    # users as verified — Google enforces verified emails server-side and
    # GitHub returns the verified primary email when the ``user:email``
    # scope is granted (see auth/oauth.py).
    if cfg.oauth_github_client_id and cfg.oauth_github_client_secret:
        github_client = make_github_client(
            cfg.oauth_github_client_id,
            cfg.oauth_github_client_secret,
        )
        app.include_router(
            fastapi_users.get_oauth_router(
                github_client,
                auth_backend,
                oauth_state_secret,
                associate_by_email=True,
                is_verified_by_default=True,
            ),
            prefix="/auth/github",
            tags=["auth"],
        )
    if cfg.oauth_google_client_id and cfg.oauth_google_client_secret:
        google_client = make_google_client(
            cfg.oauth_google_client_id,
            cfg.oauth_google_client_secret,
        )
        app.include_router(
            fastapi_users.get_oauth_router(
                google_client,
                auth_backend,
                oauth_state_secret,
                associate_by_email=True,
                is_verified_by_default=True,
            ),
            prefix="/auth/google",
            tags=["auth"],
        )

    return app
