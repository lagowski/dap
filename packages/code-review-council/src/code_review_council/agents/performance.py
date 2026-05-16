"""PerformanceAgent — real perf concerns, not premature optimisation."""

from __future__ import annotations

from typing import ClassVar

from code_review_council.agents.base import BaseAgent


class PerformanceAgent(BaseAgent):
    name: ClassVar[str] = "Performance"
    scope: ClassVar[str] = (
        "real performance issues — N+1 patterns, blocking work on hot "
        "paths, memory leaks, bundle size impact, expensive renders, "
        "cache thrashing. Skip premature optimisation."
    )
    focus_areas: ClassVar[list[str]] = [
        "N+1 patterns: list endpoint that re-fetches one row at a time; "
        "a ``for x in items: requests.get(...)`` shape; SQL or HTTP "
        "fan-out inside a render loop.",
        "Blocking on the hot path: ``time.sleep``, ``requests`` (sync) "
        "inside an async route, large in-memory transforms before a "
        "streaming response could have started.",
        "Memory: unbounded list growth across request lifetimes; class-"
        "level mutable defaults; loading whole files into memory when "
        "streaming would work; ``.all()`` on a query that could be "
        "millions of rows.",
        "React render cost: large component re-rendering on every "
        "parent state change without ``React.memo``; expensive computed "
        "value inside render without ``useMemo``; new objects/arrays "
        "passed as props on every render breaking memoisation.",
        "Bundle size: large dep imported for a single util (lodash full "
        "import instead of submodule); dev-only library leaking into "
        "production bundle; missing dynamic import for a heavy page.",
        "Cache: cache key with mutable parts that defeat reuse; cache "
        "invalidation written but never triggered on the mutation path; "
        "TTL longer than the data's freshness contract.",
        "Async correctness: ``asyncio.gather`` on tasks that all hit "
        "the same single-threaded resource (e.g. SQLite) — concurrency "
        "doesn't speed it up, it just queues.",
    ]
