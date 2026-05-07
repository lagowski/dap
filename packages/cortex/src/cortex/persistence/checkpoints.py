"""Helpers for managing LangGraph checkpoint state in PostgreSQL.

LangGraph's PostgresSaver creates and owns the ``checkpoints``,
``checkpoint_writes``, ``checkpoint_blobs``, and ``checkpoint_migrations``
tables. We don't normally touch them — but ``cortex reset`` (issue #106)
needs to clear a thread's checkpoint trail so a rejected pipeline can be
re-run from scratch instead of resuming at the last paused gate.
"""

from __future__ import annotations

import psycopg

from cortex.config.settings import load_settings

# Tables that hold per-thread state. ``checkpoint_migrations`` is shared
# schema metadata, so we leave it alone.
_THREAD_TABLES = ("checkpoints", "checkpoint_writes", "checkpoint_blobs")


def delete_thread_checkpoints(thread_id: str) -> dict[str, int]:
    """Delete all LangGraph checkpoint rows for ``thread_id``.

    Returns a per-table count of deleted rows, e.g.
    ``{"checkpoints": 12, "checkpoint_writes": 50, "checkpoint_blobs": 0}``.
    Tables that don't exist yet (checkpointer.setup() never ran) report 0
    rather than raising, so the operator can reset a thread on a fresh DB.
    """
    settings = load_settings()
    counts: dict[str, int] = {}
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        for table in _THREAD_TABLES:
            try:
                cursor = conn.execute(
                    # nosec: table names are a fixed module-level constant,
                    # not user input — psycopg can't parametrize identifiers.
                    f"DELETE FROM {table} WHERE thread_id = %s",
                    (thread_id,),
                )
                counts[table] = cursor.rowcount
            except psycopg.errors.UndefinedTable:
                counts[table] = 0
    return counts
