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


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())
