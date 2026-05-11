"""Standalone entrypoint: `uv run dap-engine` lub `python -m dap_engine`."""

from __future__ import annotations

import logging
import os

import uvicorn

from dap_engine.app import EngineConfig, create_app, parse_cors_origins


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    config = EngineConfig(
        db_path=os.environ.get("DAP_DB_PATH", "./.dap/state.db"),
        # DAP_DATABASE_URL overrides db_path when set.
        # postgresql+asyncpg://user:pass@host:port/db → PostgreSQL backend
        # Leave unset to keep SQLite (default for local dev).
        database_url=os.environ.get("DAP_DATABASE_URL"),
        host=os.environ.get("DAP_ENGINE_HOST", "127.0.0.1"),
        port=int(os.environ.get("DAP_ENGINE_PORT", "7333")),
        dry_run_budget_usd=float(os.environ.get("DAP_DRY_RUN_BUDGET_USD", "0.50")),
        # PG checkpointer pool sizing — only used when DAP_DATABASE_URL is set.
        pg_pool_min_size=int(os.environ.get("DAP_PG_POOL_MIN_SIZE", "4")),
        pg_pool_max_size=int(os.environ.get("DAP_PG_POOL_MAX_SIZE", "10")),
        # Comma-separated CORS allow-list — None falls back to the local-dev
        # default in EngineConfig (#259).
        cors_origins=parse_cors_origins(os.environ.get("DAP_CORS_ORIGINS")),
        # Auth (#299). When unset, the lifespan generates a per-process
        # random — fine for a single-instance local dev, but multi-worker
        # / multi-replica deployments MUST set DAP_AUTH_JWT_SECRET so all
        # workers validate tokens against the same key.
        auth_jwt_secret=os.environ.get("DAP_AUTH_JWT_SECRET"),
        auth_access_ttl_seconds=int(os.environ.get("DAP_AUTH_ACCESS_TTL_SECONDS", "900")),
        # When set, OAuth callbacks redirect the browser here with the
        # JWT in ``?token=`` instead of returning JSON (#300, sub-B5).
        # The dashboard's ``/api/auth/oauth/callback`` reads it and
        # promotes it to an httpOnly cookie.
        auth_oauth_redirect_url=os.environ.get("DAP_AUTH_OAUTH_REDIRECT_URL"),
        # OAuth (#299, sub-A2). Each provider activates only when both
        # client_id and client_secret are set; missing or partially-set
        # credentials are ignored without a startup error so a self-host
        # install can run with email+password only.
        oauth_github_client_id=os.environ.get("DAP_OAUTH_GITHUB_CLIENT_ID"),
        oauth_github_client_secret=os.environ.get("DAP_OAUTH_GITHUB_CLIENT_SECRET"),
        oauth_google_client_id=os.environ.get("DAP_OAUTH_GOOGLE_CLIENT_ID"),
        oauth_google_client_secret=os.environ.get("DAP_OAUTH_GOOGLE_CLIENT_SECRET"),
    )

    app = create_app(config)

    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
