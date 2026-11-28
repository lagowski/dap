"""CorrectnessAgent — logic, races, null/empty, error handling, types."""

from __future__ import annotations

from typing import ClassVar

from code_review_council.agents.base import BaseAgent


class CorrectnessAgent(BaseAgent):
    name: ClassVar[str] = "Correctness"
    scope: ClassVar[str] = (
        "logic bugs, race conditions, null/empty handling, off-by-one, "
        "error handling, transaction boundaries, type safety"
    )
    focus_areas: ClassVar[list[str]] = [
        "Logic: off-by-one in slicing or pagination; inverted conditional; "
        "early return that skips the cleanup branch.",
        "Race conditions: TOCTOU between read and write; missing transaction "
        "boundary on multi-statement DB updates; concurrent task spawning "
        "without idempotency.",
        "Null / empty: ``.get()`` then unconditional indexing; ``or`` "
        "fallback that coerces 0 / '' to the default value; empty-list "
        "iteration assumed to never fire.",
        "Error handling: bare ``except:`` swallowing every error including "
        "KeyboardInterrupt; HTTPException with the raw exception in detail "
        "(stack-trace leak); silently retrying without bounded attempts.",
        "Type safety: ``Any`` in route signatures where a concrete type is "
        "available; unsafe ``cast``/``type: ignore`` covering a real bug; "
        "Pydantic ``model_dump`` then re-validating without checking errors.",
        "Resource lifecycle: file/connection opened without ``with``; "
        "background task spawned without registration in run_registry; "
        "session not committed on the happy path.",
        "Test gaps that genuinely matter: a new branch in a critical "
        "code path with zero coverage. (Don't pad with 'no E2E test' "
        "as a recurring nag — flag once at LOW severity.)",
    ]
