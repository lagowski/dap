"""Alembic migration package (audit E6).

Ships inside the ``dap_engine`` wheel so the engine can apply
migrations at startup without a sidecar config — see
:func:`dap_engine.persistence.db._apply_alembic_migrations`.

Coexists with the legacy :mod:`dap_engine.persistence.migrations`
list. The legacy migrations run FIRST (idempotently, on every
startup) to bring pre-Alembic dev DBs forward to baseline state.
Alembic then runs from the baseline forward — its first revision
``0001_baseline`` is a no-op marker so newly-applied dev DBs already
"are at baseline" by the time Alembic looks.

New schema changes should be added via ``alembic revision
--autogenerate -m "..."`` (run from ``apps/engine/``). The legacy
``MIGRATIONS[]`` list is **frozen** as of v0.3.x — no new entries.
"""
