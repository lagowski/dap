"""Unit tests for the gemini-review dismissal helper.

Pin the pure filter that decides which prior bot reviews to dismiss
on a clean re-review. Lives in ``tests/standalone/`` (not
``tests/smoke/``) because the function is a pure list filter — no
HTTP, no DB, no engine — so it has no business pulling in the
smoke fixtures.

Why test this at all: the dismissal step is the entire fix for the
"council CHANGES_REQUESTED stuck after fix push" issue. Getting the
filter wrong has two flavours of harm — false positives (dismiss a
prior reviewer's blocker → unsafe merge) and false negatives (leave
the stale review stuck → workflow doesn't actually unblock the PR).
The filter is small enough that tests double as documentation of
the conditions.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The script lives outside the installable packages so we add the
# directory to sys.path before import. Keeps the script free of
# package boilerplate while still letting pytest reach it.
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / ".github" / "scripts"
sys.path.insert(0, str(_SCRIPT_DIR))

# ``gemini_review`` lives under ``.github/scripts/`` (not an installable
# package), so mypy can't resolve it statically — the sys.path injection
# above is a runtime-only mechanism. Suppress the import-not-found
# diagnostic; the runtime import still validates that the function
# exists.
from gemini_review import (  # type: ignore[import-not-found] # noqa: E402
    find_stale_changes_requested_review_ids,
)

BOT = "github-actions[bot]"


def _review(
    *,
    review_id: int = 100,
    state: str = "CHANGES_REQUESTED",
    login: str = BOT,
) -> dict[str, object]:
    """Minimal review-shape dict — fields not used by the filter omitted."""
    return {"id": review_id, "state": state, "user": {"login": login}}


def test_returns_empty_when_no_reviews_match() -> None:
    """No prior CHANGES_REQUESTED → nothing to dismiss. The happy path
    on the very first review of a PR."""
    assert find_stale_changes_requested_review_ids([], bot_login=BOT) == []


def test_returns_bot_changes_requested_review_ids() -> None:
    """The one case we actually exist to handle: a CHANGES_REQUESTED
    review from this same bot, sitting on the PR's reviewDecision."""
    reviews = [_review(review_id=42)]
    assert find_stale_changes_requested_review_ids(reviews, bot_login=BOT) == [42]


def test_skips_changes_requested_from_humans() -> None:
    """Critical safety check: we never dismiss a human reviewer's
    blocker. The whole point of human review is that a bot can't
    auto-clear it."""
    reviews = [_review(review_id=42, login="alice")]
    assert find_stale_changes_requested_review_ids(reviews, bot_login=BOT) == []


def test_skips_other_states() -> None:
    """APPROVED / COMMENTED / DISMISSED reviews aren't blockers, so
    re-dismissing them would be churn at best, footgun at worst
    (re-dismissing an already-DISMISSED review on every workflow run
    would spam the PR timeline)."""
    reviews = [
        _review(review_id=1, state="APPROVED"),
        _review(review_id=2, state="COMMENTED"),
        _review(review_id=3, state="DISMISSED"),
    ]
    assert find_stale_changes_requested_review_ids(reviews, bot_login=BOT) == []


def test_returns_multiple_when_multiple_match() -> None:
    """A PR can accumulate multiple bot CHANGES_REQUESTED reviews
    across fix-cycles. The filter must return them all so dismissal
    converges in a single workflow run."""
    reviews = [
        _review(review_id=10),
        _review(review_id=11, login="alice"),  # human — skipped
        _review(review_id=12),
    ]
    out = find_stale_changes_requested_review_ids(reviews, bot_login=BOT)
    assert out == [10, 12]


def test_tolerates_malformed_review_entries() -> None:
    """``gh api`` is normally well-shaped but we'd rather degrade
    gracefully than crash the workflow on an upstream surprise.
    Non-dict items, missing ``user`` keys, and non-int ids all
    silently drop out of the result."""
    reviews: list[object] = [
        "not a dict",
        {"state": "CHANGES_REQUESTED"},  # no user
        {"state": "CHANGES_REQUESTED", "user": "not a dict"},
        {"state": "CHANGES_REQUESTED", "user": {"login": BOT}},  # no id
        {
            "id": "not-an-int",
            "state": "CHANGES_REQUESTED",
            "user": {"login": BOT},
        },
        _review(review_id=99),  # the one valid entry
    ]
    # mypy sees the function as ``Any`` (the script lives outside the
    # importable package tree — see the import line above), so the
    # ``list[object]`` argument doesn't need a per-call cast. The
    # runtime behaviour is what we're actually pinning here.
    out = find_stale_changes_requested_review_ids(reviews, bot_login=BOT)
    assert out == [99]


def test_bot_login_match_is_exact() -> None:
    """A near-miss login string must NOT match — the bot login is
    fully qualified ``github-actions[bot]`` and partial matches would
    risk dismissing a similarly-named app's reviews."""
    reviews = [
        _review(review_id=1, login="github-actions"),  # missing [bot] suffix
        _review(review_id=2, login="copilot-pull-request-reviewer"),
        _review(review_id=3, login=BOT),
    ]
    out = find_stale_changes_requested_review_ids(reviews, bot_login=BOT)
    assert out == [3]
