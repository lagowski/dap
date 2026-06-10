"""Centralized logging configuration for the engine (#778 Phase 2).

Single home for the root-logger setup that previously lived inline in
``__main__.py``. Call :func:`configure_logging` exactly once at process
startup; per-module loggers keep using
``logging.getLogger("dap.engine.<area>")`` as before.
"""

from __future__ import annotations

import logging
import os

__all__ = ["LOG_FORMAT", "configure_logging"]

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
LOG_DATEFMT = "%H:%M:%S"

# Env var an operator can set to lift the engine to DEBUG (or quiet it
# to WARNING) without a code change. Explicit ``level=`` argument wins.
LOG_LEVEL_ENV_VAR = "DAP_LOG_LEVEL"


def _resolve_level(level: int | str | None) -> int | str:
    """Resolve the effective level: explicit arg > env var > INFO.

    Raises ``ValueError`` for an unknown level name — a typo in
    ``DAP_LOG_LEVEL`` should fail startup loudly, matching the engine's
    refuse-to-start policy for other config errors (half-migrated
    schema, missing Fernet key on write paths).
    """
    if level is None:
        level = os.environ.get(LOG_LEVEL_ENV_VAR, "").strip() or logging.INFO
    if isinstance(level, str):
        candidate = level.upper()
        if candidate not in logging.getLevelNamesMapping():
            raise ValueError(
                f"Unknown log level {level!r} (from {LOG_LEVEL_ENV_VAR}?) — "
                "expected one of DEBUG/INFO/WARNING/ERROR/CRITICAL"
            )
        return candidate
    return level


def configure_logging(level: int | str | None = None) -> None:
    """Configure the root logger for the engine process.

    ``force=True`` keeps the call idempotent and deterministic: repeated
    calls (or a pre-configured root logger inherited from a host
    process) replace handlers instead of stacking duplicates.
    """
    logging.basicConfig(
        level=_resolve_level(level),
        format=LOG_FORMAT,
        datefmt=LOG_DATEFMT,
        force=True,
    )
