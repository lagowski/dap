"""FrontendAgent — React / Next.js App Router specifics.

Skip-gate: if the diff doesn't touch ``.tsx?`` / ``.jsx?`` / ``.css``,
this agent has nothing to say. Save the API call.
"""

from __future__ import annotations

import re
from typing import ClassVar

from code_review_council.agents.base import BaseAgent

_FE_FILE_RE = re.compile(
    r"^\+\+\+ b/.*\.(tsx?|jsx?|css|scss|mdx)$",
    re.MULTILINE,
)


class FrontendAgent(BaseAgent):
    name: ClassVar[str] = "Frontend"
    scope: ClassVar[str] = (
        "React / Next.js App Router patterns — Server vs Client Component "
        "boundaries, hooks rules, hydration mismatches, navigation "
        "gotchas, error boundaries, accessibility for keyboard / screen "
        "readers"
    )
    focus_areas: ClassVar[list[str]] = [
        'Client / Server Component boundary: ``"use client"`` missing '
        "on a file that uses hooks; ``async`` on a Client Component "
        "(Server-Component-only feature); importing a server-only "
        "module into a client tree.",
        "Hooks rules: hooks called conditionally / inside loops; "
        "``useEffect`` missing deps that should be in the array; "
        "``useState`` initializer running expensive work on every "
        "render instead of inside a function.",
        "App Router gotchas: ``router.push`` to the current pathname "
        "(silent no-op); ``router.refresh`` not paired with ``reset()`` "
        "in error boundaries; hard ``window.location`` ignoring "
        "``basePath``.",
        "Hydration mismatches: ``new Date()``, ``Math.random()``, or "
        "``window`` usage in a component rendered on the server; "
        "conditional render that depends on browser-only state.",
        "Error boundaries: missing ``reset`` invocation after navigation "
        "(shared boundaries persist across intra-segment routes); "
        "``window.location.reload`` on an OAuth-callback page "
        "resubmitting single-use ``?code=`` params.",
        "Real accessibility for keyboard/SR (not AAA nits): missing "
        "focus trap in a modal, missing ``aria-label`` on icon-only "
        'buttons, ``role="alert"`` scoped to a giant container, '
        "no keyboard handler on a non-button clickable.",
        "Form patterns: uncontrolled inputs reading ``defaultValue`` "
        "in a controlled wrapper; submit handler missing "
        "``preventDefault``; validation errors not announced to AT.",
    ]

    @classmethod
    def should_run(cls, diff: str) -> bool:
        return bool(_FE_FILE_RE.search(diff))
