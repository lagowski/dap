# dap-database

Shared SQLAlchemy plumbing for DAP (#778 Phase 1). Import path: `dap_database`.

One home for the pieces previously duplicated between
`dap_engine.persistence.db` (sync) and `dap_engine.auth.db` (async):

- **URL/dialect helpers** — `detect_dialect`, `normalize_pg_prefix`,
  `pg_sync_url`, `pg_conn_string`, `redact_database_url`, `async_url_for`
- **Raw engine factories** — `create_sqlite_engine` (WAL pragmas, busy
  timeout), `create_postgresql_engine` (psycopg v3, pre-ping, recycle),
  `create_async_engine_for_url`
- **Session factories** — `make_session_factory`,
  `make_async_session_factory`, `session_scope`

## What deliberately stays out

- **ORM models and migrations** — schema ownership stays with the engine
  (`dap_engine.persistence`). The factories here create *no* tables.
- **DB drivers** — `aiosqlite` / `psycopg` are consumer dependencies;
  this package only requires `sqlalchemy`.

```python
from dap_database import create_sqlite_engine, make_session_factory, session_scope

engine = create_sqlite_engine("/data/state.db")
factory = make_session_factory(engine)
with session_scope(factory) as session:
    ...
```
