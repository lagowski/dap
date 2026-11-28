"""DatabaseAgent — SQL injection, transactions, ORM misuse, schema mistakes.

Overlaps slightly with Correctness (race conditions) and Security
(injection), but the database lens is specific enough that giving it
its own agent measurably improves hit-rate on persistence bugs. The
arbiter dedupes when two agents flag the same line.

Skip-gate: if the diff doesn't touch Python (apps/engine), SQL, or
migration files, this agent has nothing to say. Save the API call.
"""

from __future__ import annotations

import re
from typing import ClassVar

from code_review_council.agents.base import BaseAgent

# Regex matching diff hunk headers for files that could plausibly
# contain database work. Engine + migration + raw SQL files.
_DB_FILE_RE = re.compile(
    r"^\+\+\+ b/(apps/engine/.*\.py|packages/.+/persistence/.*\.py|.*\.sql|.*/migrations/.*\.py)$",
    re.MULTILINE,
)


class DatabaseAgent(BaseAgent):
    name: ClassVar[str] = "Database"
    scope: ClassVar[str] = (
        "SQL queries, transaction boundaries, ORM correctness, schema "
        "migrations, connection lifecycle, query performance shape "
        "(N+1, missing indexes), data integrity"
    )
    focus_areas: ClassVar[list[str]] = [
        # Concrete pattern bullets — each is something the model can
        # scan the diff for directly without inferential leaps.
        "SQL injection: f-string or % interpolation inside ``text(...)`` "
        "or ``cursor.execute``; user input concatenated into a raw query.",
        "Transaction boundaries: multi-statement DB updates outside a "
        "``with session.begin()`` block; commit on the happy path but "
        "no rollback on the error path; session.commit called twice.",
        "ORM correctness: ``Session.execute(select(...))`` without "
        "``.scalars()`` when expecting a list of rows; ``.one_or_none()`` "
        "when callers expect a single result; lazy-load on a detached "
        "object that will fail later in the request.",
        "N+1 queries: list-rendering loop that re-fetches related rows "
        "per item; missing ``selectinload`` / ``joinedload`` on a list "
        "endpoint; ``for row in rows: row.related.thing``.",
        "Schema migrations: ALTER TABLE without nullable handling on "
        "existing rows; index added on a column never queried; column "
        "renamed without a backfill step.",
        "Connection lifecycle: session created but never closed; "
        "engine instantiated per request instead of cached at app "
        "scope; sync DB call inside async handler without ``to_thread``.",
        "Data integrity: unique constraint missing where business logic "
        "assumes uniqueness; foreign key without ``ondelete`` policy "
        "causing orphan rows or unexpected cascades.",
    ]

    @classmethod
    def should_run(cls, diff: str) -> bool:
        return bool(_DB_FILE_RE.search(diff))
