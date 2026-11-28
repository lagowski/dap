"""Re-review skip via hidden marker comments in posted review bodies.

Why: without this, every workflow re-trigger on the same head SHA re-bills
the LLM provider. ``pull_request: synchronize`` fires per push, but
``workflow_dispatch`` reruns + future cron schedules would duplicate work
on identical SHAs. The marker pattern makes the gate idempotent per head
commit — cost stays flat regardless of trigger cadence.

Pattern (ported from Speecher's ``copilot-review-required.yml``, PRs #490
+ #496):

1. Embed two hidden HTML comments in every posted review body::

        <!-- ai-review:<40-hex-head-sha> -->
        <!-- ai-verdict:approve|reject|malformed -->

2. Before kicking a fresh review, list the PR's reviews (paginated) and
   scan for our own marker on the CURRENT head SHA.
3. If found AND the review author is our bot — short-circuit. Caller
   refreshes the check_run with the cached verdict instead of re-running
   the council.

Hardening details:

- ``_MARKER_RE`` requires exactly 40 hex chars so an attacker-crafted
  longer marker can't soak up a permissive ``[^>]*``.
- Author filter (``login == "github-actions[bot]"`` OR ``user.type ==
  "Bot"``) prevents a human quoting our marker in their own review body
  from triggering a false skip — Speecher PR #496 caught exactly this.
- The verdict tag defaults to ``"approve"`` when missing so older
  pre-tag reviews still resolve to a sensible cached outcome.

Callers are responsible for paginating the review list (long-lived PRs
with >100 reviews will scroll the marker off page 1 otherwise). With the
GitHub CLI: ``gh api --paginate repos/{owner}/{repo}/pulls/{n}/reviews``.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# Strict 40-hex marker. ``re.IGNORECASE`` because git SHAs are
# canonically lowercase but a future caller passing uppercase shouldn't
# silently miss the match.
_MARKER_RE = re.compile(r"<!--\s*ai-review:([a-f0-9]{40})\s*-->", re.IGNORECASE)
_VERDICT_RE = re.compile(
    r"<!--\s*ai-verdict:(approve|reject|malformed)\s*-->",
    re.IGNORECASE,
)

# GitHub Actions' built-in token posts under this login when run from a
# default workflow. Custom bots (GitHub Apps installed in the repo) post
# under ``<app-name>[bot]``. We accept either explicit login OR
# ``user.type == "Bot"`` so both shapes work.
DEFAULT_BOT_LOGIN = "github-actions[bot]"


class CachedReview(NamedTuple):
    """A bot review already posted against the current head SHA.

    Caller maps ``verdict`` to GitHub's ``check_run.conclusion`` field —
    typically ``approve → success``, ``reject → failure``, ``malformed
    → neutral``. The mapping lives in the workflow script, not here,
    because conclusion semantics depend on branch-protection setup.
    """

    review_id: int
    head_sha: str
    verdict: str
    submitted_at: str


def make_marker(head_sha: str) -> str:
    """Hidden HTML comment to embed in every posted review body.

    Format is verbatim Speecher's so an org-wide audit log query can
    match across repos without per-tool dialects.
    """
    return f"<!-- ai-review:{head_sha} -->"


def make_verdict_tag(verdict: str) -> str:
    """Companion tag for :func:`make_marker`.

    ``verdict`` must be one of ``approve|reject|malformed``. We reject
    other values at call time rather than silently sanitize because a
    typo here would persist in posted reviews and skew the cache.
    """
    allowed = {"approve", "reject", "malformed"}
    if verdict not in allowed:
        raise ValueError(
            f"verdict must be one of {sorted(allowed)!r}, got {verdict!r}",
        )
    return f"<!-- ai-verdict:{verdict} -->"


def find_cached_review(
    reviews: list[dict[str, object]],
    *,
    head_sha: str,
    bot_login: str = DEFAULT_BOT_LOGIN,
) -> CachedReview | None:
    """Scan a PR's review list for our own marker on ``head_sha``.

    Caller passes the FULL paginated list — do NOT slice. On long-lived
    PRs with >100 reviews the marker would otherwise scroll off page 1.

    Args:
        reviews: list of GitHub review dicts (``id``, ``body``,
            ``submitted_at``, ``user`` keys). Either ``gh api --paginate``
            output or a PyGithub-style list of raw_data dicts works.
        head_sha: current PR head commit SHA. The cache only hits when
            the marker matches this SHA — any new push invalidates.
        bot_login: login string of the workflow's bot. Defaults to the
            built-in Actions token's login; override for installed apps.

    Returns:
        ``CachedReview`` on hit (caller short-circuits to a refreshed
        check_run); ``None`` on miss (caller runs a fresh review).

    The author filter is the security boundary: without it, a malicious
    PR author could paste our marker into their own review body and
    trick the skip. With it, only reviews posted under the bot login
    (or marked ``user.type == "Bot"`` for app-installed bots) count.
    """
    head_sha_lower = head_sha.lower()
    for r in reviews:
        body = r.get("body")
        if not isinstance(body, str):
            continue

        user = r.get("user")
        if not isinstance(user, dict):
            continue
        login_ok = user.get("login") == bot_login
        type_ok = user.get("type") == "Bot"
        if not (login_ok or type_ok):
            continue

        m = _MARKER_RE.search(body)
        if not m or m.group(1).lower() != head_sha_lower:
            continue

        vmatch = _VERDICT_RE.search(body)
        verdict = vmatch.group(1).lower() if vmatch else "approve"

        raw_id = r.get("id")
        # GitHub returns review IDs as ints in the JSON payload; the
        # explicit narrowing keeps mypy honest and gives us a sane
        # skip path if a future API change returns strings instead.
        if isinstance(raw_id, (int, str)):
            review_id = int(raw_id)
        else:
            continue

        submitted_at = r.get("submitted_at")
        return CachedReview(
            review_id=review_id,
            head_sha=head_sha,
            verdict=verdict,
            submitted_at=submitted_at if isinstance(submitted_at, str) else "",
        )
    return None
