# dap-engine

FastAPI + SQLAlchemy 2.0 + SQLite (WAL mode). LangGraph integration w F5.

## Endpoints (F0)

```
GET /health                       → service status
GET /runtimes                     → list of registered adapters
GET /runtimes/{id}/health         → per-adapter healthcheck
```

## Run standalone

```bash
uv run dap-engine
# default: 127.0.0.1:7333, db: ./.dap/state.db
```

## Migrations

```bash
cd apps/engine
uv run alembic revision --autogenerate -m "msg"
uv run alembic upgrade head
```
