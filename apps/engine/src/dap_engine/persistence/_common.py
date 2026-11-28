"""Shared persistence-layer primitives.

These helpers and the ``NotFoundError`` exception are imported by every
per-entity module (agents, pipelines, projects, runs) and re-exported
from :mod:`dap_engine.persistence.repository` for backward compat with
existing callers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


class NotFoundError(Exception):
    """Raised when an entity is not found by id/version."""


class ConflictError(Exception):
    """Raised when an operation conflicts with the entity's current state.

    Maps to HTTP 409 at the API layer — e.g. trying to delete a run that is
    still in-flight (must be aborted first).
    """


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())
