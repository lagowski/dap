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

W F0 schema jest tworzona automatycznie przy starcie engine'u przez
`Base.metadata.create_all(engine)` (dev mode).

Pełne Alembic-based migrations są **planowane na późniejszą iterację**. Repo nie
zawiera jeszcze scaffoldu Alembic (`alembic.ini`, `alembic/env.py`), więc
komendy `alembic revision` / `alembic upgrade` nie będą działać dopóki ten
config nie zostanie dodany.
