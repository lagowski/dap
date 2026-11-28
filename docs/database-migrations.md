# Database Migrations

DAP uses two migration mechanisms during engine startup:

1. `Base.metadata.create_all(engine)` creates tables that are missing from a
   fresh database.
2. `dap_engine.persistence.migrations.apply_migrations()` runs the frozen
   legacy in-code migration ledger.
3. `dap_engine.persistence.db._apply_alembic_migrations()` runs Alembic
   `upgrade head` against the same engine.

This order is intentional. `create_all()` handles fresh databases, the legacy
ledger keeps older pre-Alembic SQLite databases upgradeable, and Alembic owns
all schema changes after the baseline.

## Legacy Migration Policy

`apps/engine/src/dap_engine/persistence/migrations.py` is frozen. Do not append
new entries to `MIGRATIONS[]`.

The legacy ledger still runs on every startup because existing installations may
have been created before Alembic existed. Its migration bodies are idempotent:
on a fresh database most bodies short-circuit because `create_all()` has already
created the current schema, but their names are still recorded in
`schema_migrations`.

The frozen ledger is protected by
`tests/smoke/test_alembic_integration.py::test_legacy_migrations_list_frozen_at_18`.
If this count changes, the test should fail. Treat that as a design stop, not as
a prompt to update the number.

## Alembic Policy

New schema changes are Alembic-only. Add a new revision under
`apps/engine/src/dap_engine/alembic/versions/` for every table, column, index,
constraint, or data-shape change that affects persisted application state.

Alembic revisions ship inside the `dap_engine` package so installed wheels can
migrate themselves at startup. The developer CLI config lives at
`apps/engine/alembic.ini`; runtime startup builds an Alembic `Config`
programmatically and does not depend on that file.

The baseline revision, `0001_baseline`, is a no-op marker for the schema state
after the frozen legacy ledger. Do not add operations to it. Later revisions
chain from it and are applied by normal Alembic upgrade order.

`tests/smoke/test_alembic_integration.py` protects both sides of the handoff:
the frozen legacy migration count, the no-op baseline revision, and the current
Alembic head recorded in `alembic_version` after startup.

## Adding a Migration

1. Update the SQLAlchemy ORM model and any repository/API code that needs the
   new field or table.
2. From the repository root, generate a revision:

   ```bash
   uv run --directory apps/engine --frozen alembic revision --autogenerate -m "short description"
   ```

3. Review the generated file under
   `apps/engine/src/dap_engine/alembic/versions/`.
4. Make the revision idempotent where startup ordering requires it. Fresh
   databases already run `create_all()` before Alembic, so create-table and
   add-column revisions should tolerate the object already existing.
5. Keep downgrade behavior consistent with the existing revision chain. When a
   downgrade is not operationally supported, make that explicit in the revision.
6. Run the migration smoke tests:

   ```bash
   uv run pytest tests/smoke/test_alembic_integration.py
   ```

7. For index changes, update `docs/database-indexes.md` with the query shape
   that justifies the index.

Do not edit `schema_migrations` directly. Do not delete legacy migrations. Do
not create a schema-changing PR that relies only on `create_all()`.

## Operator Notes

Engine startup applies migrations automatically. Operators normally do not need
to run `alembic upgrade head` manually.

For out-of-band maintenance, the Alembic CLI can be run from `apps/engine/`
against the configured database, but take a database backup first. DAP migrations
are forward-first; rollback is an operational restore from backup unless a
specific revision documents a safe downgrade path.
