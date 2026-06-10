"""Standalone entrypoint: `uv run dap-engine` lub `python -m dap_engine`."""

from __future__ import annotations

import os

import uvicorn

from dap_engine.app import EngineConfig, create_app, parse_cors_origins
from dap_engine.logging import configure_logging


def main() -> None:
    configure_logging()

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
        pg_pool_reconnect_timeout=float(os.environ.get("DAP_PG_POOL_RECONNECT_TIMEOUT", "300")),
        # Comma-separated CORS allow-list — None falls back to the local-dev
        # default in EngineConfig (#259).
        cors_origins=parse_cors_origins(os.environ.get("DAP_CORS_ORIGINS")),
        # Auth (#299). When unset, the lifespan generates a per-process
        # random — fine for a single-instance local dev, but multi-worker
        # / multi-replica deployments MUST set DAP_AUTH_JWT_SECRET so all
        # workers validate tokens against the same key.
        auth_jwt_secret=os.environ.get("DAP_AUTH_JWT_SECRET"),
        auth_access_ttl_seconds=int(os.environ.get("DAP_AUTH_ACCESS_TTL_SECONDS", "900")),
        # Reset-token logging — opt-in dev convenience documented on
        # ``EngineConfig.auth_log_reset_tokens``. Off unless the env
        # var is set to a truthy string. The /admin/settings page
        # surfaces the resulting value with an amber warning when it's
        # on.
        auth_log_reset_tokens=os.environ.get("DAP_AUTH_LOG_RESET_TOKENS", "").strip().lower()
        in ("1", "true", "yes", "on"),
        # When set, OAuth callbacks redirect the browser here with the
        # JWT in ``?token=`` instead of returning JSON (#300, sub-B5).
        # The dashboard's ``/api/auth/oauth/callback`` reads it and
        # promotes it to an httpOnly cookie.
        #
        # Normalise blank / whitespace-only values to ``None`` — an
        # operator who exports the var but leaves it empty would
        # otherwise hand fastapi-users an invalid empty redirect_url
        # (Copilot review on PR #326).
        auth_oauth_redirect_url=(os.environ.get("DAP_AUTH_OAUTH_REDIRECT_URL", "").strip() or None),
        # OAuth (#299, sub-A2). Each provider activates only when both
        # client_id and client_secret are set; missing or partially-set
        # credentials are ignored without a startup error so a self-host
        # install can run with email+password only.
        oauth_github_client_id=os.environ.get("DAP_OAUTH_GITHUB_CLIENT_ID"),
        oauth_github_client_secret=os.environ.get("DAP_OAUTH_GITHUB_CLIENT_SECRET"),
        oauth_google_client_id=os.environ.get("DAP_OAUTH_GOOGLE_CLIENT_ID"),
        oauth_google_client_secret=os.environ.get("DAP_OAUTH_GOOGLE_CLIENT_SECRET"),
        # Template registry (#385). CSV of trusted hostnames the
        # ``/pipelines/import-from-url`` endpoint will fetch from.
        # Empty (default) → endpoint returns 422 (opt-in feature).
        template_registry_allowed_hosts=[
            h.strip()
            for h in os.environ.get("DAP_TEMPLATE_REGISTRY_ALLOWED_HOSTS", "").split(",")
            if h.strip()
        ],
        template_registry_auth_token=(
            os.environ.get("DAP_TEMPLATE_REGISTRY_AUTH_TOKEN", "").strip() or None
        ),
        # Fernet key for the instance env-var store (#388). Must persist
        # across restarts — encrypted rows written today must decrypt
        # tomorrow. Generate with:
        #   python -c 'from cryptography.fernet import Fernet; \
        #              print(Fernet.generate_key().decode())'
        # When unset the admin write paths refuse (503), so the operator
        # can't accidentally store rows that won't decrypt on restart.
        instance_env_vars_key=(os.environ.get("DAP_INSTANCE_ENV_VARS_KEY", "").strip() or None),
        allow_bash_runtime_for_non_admin=(
            os.environ.get("DAP_ALLOW_BASH_RUNTIME_FOR_NON_ADMIN", "").strip().lower()
            in ("1", "true", "yes", "on")
        ),
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
