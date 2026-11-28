"""Persistence helpers for instance env vars (audit E3).

The settings route used to do four direct ``session.execute(
select(InstanceEnvVarORM)…)`` calls of its own, bypassing the
per-entity persistence layer that every other resource (agents,
pipelines, projects, runs) consistently goes through. This module
closes that gap.

What lives here vs. what stays in the route:

- **Here** — SELECT primitives + the bare ``session.delete`` call.
  Returns ORM rows; raises :class:`NotFoundError` when a single-row
  lookup misses. No knowledge of HTTP, encryption, or audit events.
- **Route** — Pydantic schemas, ``HTTPException``, value encryption,
  audit-event recording, response shaping. The route is what knows
  about the wire protocol; persistence is what knows about the
  table.

The bulk lookup ``find_env_vars_by_keys`` is the load-bearing
optimisation behind the POST handler — it folds N per-key SELECTs
into a single ``WHERE key IN (...)``. Removing it would re-introduce
the N+1 the route comment originally called out, so the function
docstring pins the contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from dap_engine.persistence._common import NotFoundError
from dap_engine.persistence.models import InstanceEnvVarORM


def list_env_vars(session: Session) -> Sequence[InstanceEnvVarORM]:
    """Return every instance env var row, ordered by key.

    Stable ordering makes the dashboard list deterministic and means
    snapshot tests don't have to sort the response themselves.
    """
    return (
        session.execute(select(InstanceEnvVarORM).order_by(InstanceEnvVarORM.key)).scalars().all()
    )


def find_env_vars_by_keys(
    session: Session,
    keys: Iterable[str],
) -> dict[str, InstanceEnvVarORM]:
    """Bulk-load rows for the given keys, returning ``{key: row}``.

    The dict mapping is what the upsert path actually wants — it
    needs O(1) "does this key already exist?" lookups while iterating
    the incoming body. Callers passing an empty ``keys`` get back an
    empty dict without issuing a SQL call (sqlalchemy's ``IN ()`` is
    a portability footgun on some dialects, so we short-circuit).

    This is the N+1 fold-up: one ``WHERE key IN (...)`` instead of
    one round-trip per key. Bulk upserts in particular depend on it.
    """
    key_list = list(keys)
    if not key_list:
        return {}
    rows = (
        session.execute(select(InstanceEnvVarORM).where(InstanceEnvVarORM.key.in_(key_list)))
        .scalars()
        .all()
    )
    return {row.key: row for row in rows}


def get_env_var_by_key(session: Session, key: str) -> InstanceEnvVarORM:
    """Return the row for ``key`` or raise :class:`NotFoundError`.

    Routes that want to translate "missing" into HTTP 404 should
    catch ``NotFoundError`` and raise their own ``HTTPException`` —
    keeps HTTP semantics out of the persistence layer.
    """
    row = session.execute(
        select(InstanceEnvVarORM).where(InstanceEnvVarORM.key == key)
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"Instance env var not found: {key!r}")
    return row


def delete_env_var_by_key(session: Session, key: str) -> None:
    """Soft find + ``session.delete`` for ``key``. Raises ``NotFoundError``.

    The commit (or rollback on raise) is the caller's responsibility
    — we operate on the session passed in, which is the same one the
    route's audit-event helper writes to. Keeping both writes inside
    one transaction guarantees a deleted row and its audit event
    land atomically (or neither does).
    """
    row = get_env_var_by_key(session, key)
    session.delete(row)
