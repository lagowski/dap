"""Drive the Code Review Council against a PR + post the result back.

Thin adapter between GitHub Actions and the
:mod:`code_review_council` package. Reads the diff via ``gh pr diff``,
builds a ``ProjectContext`` tailored to DAP, runs the Council, and
posts the verdict as a real PR review plus a ``gemini-review``
check_run so branch protection has something to gate on.

V2 wiring (Council + OpenRouter + hardening ports):
    - **Provider**: default ``OpenRouterProvider`` when
      ``OPENROUTER_API_KEY`` is set; fall back to ``GeminiProvider``
      when only ``GEMINI_API_KEY`` is present. The OpenRouter route
      lets us point at deepseek/Claude/GPT (and rotate without
      redeploying) — Gemini stays as a free-tier fallback.
    - **Marker skip**: before any LLM call, paginate the PR's reviews
      and look for our own marker on the current head SHA. Hit →
      refresh the check_run with the cached verdict, save the API
      call. Miss → fresh council run.
    - **Input sanitization**: ``safe_title`` / ``safe_body`` strip
      marker syntax and fence escapes from PR-author content before
      it enters the prompt, defending against marker poisoning and
      prompt injection.

Environment:
    OPENROUTER_API_KEY  Preferred. OpenRouter API key (preferred
                         path — multi-model gateway).
    GEMINI_API_KEY      Fallback. Google AI Studio key (used when
                         OPENROUTER_API_KEY unset).
    GH_TOKEN            Required. Workflow ``GITHUB_TOKEN`` scoped to
                         ``pull-requests: write`` + ``checks: write``.
    GITHUB_REPOSITORY   Auto-injected — ``owner/repo``.
    PR_NUMBER           Required. Plumbed from the workflow event.
    OPENROUTER_MODEL    Optional. Overrides the council's default
                         model on the OpenRouter route.
    GEMINI_MODEL        Optional. Overrides the model on the Gemini
                         fallback route. Defaults to
                         ``gemini-2.5-flash``.
    COUNCIL_BOT_LOGIN   Optional. Login string our reviews post as
                         (default ``github-actions[bot]``); override
                         when running as a custom installed App.

The reviewer prompts live INSIDE the council package's agent
classes — see ``packages/code-review-council/src/code_review_council/
agents/*.py``. To tweak tone/scope edit those files (or add a new
agent) rather than reaching into this script.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from code_review_council import Council, Finding, ProjectContext, ReviewVerdict
from code_review_council.marker import (
    DEFAULT_BOT_LOGIN,
    find_cached_review,
    make_marker,
    make_verdict_tag,
)
from code_review_council.providers import GeminiProvider, OpenRouterProvider
from code_review_council.providers.base import BaseProvider
from code_review_council.sanitize import safe_body, safe_title, strip_marker_tags

CHECK_NAME = "gemini-review"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
MAX_DIFF_CHARS = 60_000

# Map our verdict strings to the (a) GitHub review event flag and (b)
# check_run conclusion. Centralized so the cached-review refresh path
# and the fresh-review path can't drift.
_VERDICT_TO_REVIEW_FLAG = {
    "request_changes": "--request-changes",
    "comment": "--comment",
}

# Cached-marker verdict → check_run conclusion. ``malformed`` means the
# prior posted review had our marker tag but couldn't be parsed cleanly
# — we treat that as a soft warning (``neutral``), not a green light.
# Hard-coded mapping so a missing key (future verdict value added to
# the marker vocabulary but forgotten here) defaults to ``neutral`` as
# the safe fallback rather than silently approving.
_CACHED_VERDICT_TO_CONCLUSION = {
    "approve": "success",
    "reject": "failure",
    "malformed": "neutral",
}


# ---------------------------------------------------------------------------
# DAP project context — the central anti-hallucination knob
# ---------------------------------------------------------------------------


def _dap_context() -> ProjectContext:
    """Project facts the council needs to scope findings correctly.

    Edit the ``out_of_scope`` list when the project's stack changes —
    e.g. if DAP ever adds i18n, drop it from here so the council can
    flag i18n bugs again. Mid-PR tweaks should be rare; this is a
    repo-level config.
    """
    return ProjectContext(
        stack=[
            "Python 3.13 / FastAPI / SQLAlchemy / LangGraph (apps/engine)",
            "Next.js 14 App Router / TypeScript 5 / shadcn/ui / Tailwind (apps/dashboard)",
            "pytest smoke + Playwright e2e (tests/smoke + e2e)",
            "monorepo workspace: apps/{cli,engine,dashboard}, "
            "packages/{types,runtimes,prompt-dsl,schemas,code-review-council}",
        ],
        in_scope=[
            "auth / authorization / anti-enumeration",
            "data correctness (SQL, race conditions, transaction boundaries)",
            "real error handling (uncaught throws, retry semantics)",
            "type safety (no ``Any`` in route signatures, no unsafe casts)",
            "real performance (N+1, blocking renders, memory leaks)",
            "real a11y for keyboard / screen reader / focus",
            "tests that genuinely cover new behaviour",
        ],
        out_of_scope=[
            "i18n",
            "internationalization",
            "locale",
            "RTL",
            "dir attribute",
            "CSP nonce",
            "unsafe-inline",
            "AAA accessibility",
            'type="button" on shadcn',
            "defensive nits on framework defaults",
        ],
        notes=(
            "Admin endpoints return 404 (not 403) for non-admins "
            "as anti-enumeration. Use the ``require_admin_user`` dep, "
            "not a hand-rolled ``if not user.is_superuser`` check. "
            "Audit events go through ``record_audit_event`` typed with "
            "the ``AuditEventType`` Literal — string literals are a "
            "smell. ``client`` / ``authed_client`` fixtures live in "
            "``tests/smoke/conftest.py``; new tests should not re-roll "
            "the fixture locally. ``shadcn`` defaults (Button, Dialog, "
            "etc.) are fine — don't flag missing type=button or other "
            "defensive nits on framework primitives."
        ),
    )


# ---------------------------------------------------------------------------
# Provider selection — OpenRouter preferred, Gemini fallback
# ---------------------------------------------------------------------------


def _select_provider() -> tuple[BaseProvider, str]:
    """Return ``(provider, model_label)`` for the current environment.

    Preference order:
      1. ``OPENROUTER_API_KEY`` set → ``OpenRouterProvider`` with the
         model from ``OPENROUTER_MODEL`` (or the provider's default).
      2. ``GEMINI_API_KEY`` set → ``GeminiProvider``, model from
         ``GEMINI_MODEL`` (default ``gemini-2.5-flash``).
      3. Neither set → caller exits with a clear error.

    The ``model_label`` returned is the actual model string that ended
    up in the request; it's stamped into the review-body footer for
    audit. We don't hard-code ``OpenRouterProvider``'s default here
    because that constant lives in the provider module and could
    change — we just read it back from ``provider.name`` or the env
    var that drove it.
    """
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if or_key:
        model = os.environ.get("OPENROUTER_MODEL", "").strip() or None
        provider = (
            OpenRouterProvider(api_key=or_key, model=model)
            if model
            else OpenRouterProvider(api_key=or_key)
        )
        # OpenRouterProvider exposes its resolved model on the
        # ``.name`` property (the underlying field is ``_model``, but
        # ``name`` is the public surface — see the BaseProvider
        # protocol). Use it directly rather than a ``getattr`` probe,
        # which silently fell back to a stale default string and
        # produced the misleading "openrouter:openrouter:default"
        # footer flagged by the council.
        return provider, f"openrouter:{provider.name}"

    gem_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if gem_key:
        model = os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
        return GeminiProvider(api_key=gem_key, model=model), model

    print(
        "::error::Neither OPENROUTER_API_KEY nor GEMINI_API_KEY is set",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# gh CLI shells
# ---------------------------------------------------------------------------


def gh_capture(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def gh_run(*args: str, input_data: str | None = None) -> None:
    subprocess.run(
        ["gh", *args],
        input=input_data,
        text=True,
        check=True,
    )


def get_pr_meta(pr_number: int) -> dict[str, object]:
    payload = gh_capture(
        "pr",
        "view",
        str(pr_number),
        "--json",
        "title,body,headRefOid,additions,deletions,changedFiles",
    )
    return json.loads(payload)  # type: ignore[no-any-return]


def get_diff(pr_number: int) -> str:
    return gh_capture("pr", "diff", str(pr_number))


def list_pr_reviews(repo: str, pr_number: int) -> list[dict[str, object]]:
    """Return ALL reviews on the PR via ``gh api --paginate``.

    Paginated because long-lived PRs with >100 reviews would otherwise
    scroll our marker off page 1 and miss the skip cache. The
    ``--paginate`` flag concatenates pages into one JSON array which
    is exactly what :func:`find_cached_review` consumes.

    Validates the parsed shape — if GitHub ever returns an error
    object (``{"message": "..."}``) instead of an array, we'd
    otherwise pass it to ``find_cached_review`` which iterates and
    would fail with a confusing ``TypeError``. Better to surface the
    real shape early.
    """
    raw = gh_capture(
        "api",
        "--paginate",
        f"repos/{repo}/pulls/{pr_number}/reviews",
    )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        # ``gh api`` returning non-JSON text (rare — usually means the
        # CLI hit an HTML error page or an unauth shell). Treat as a
        # cache miss so the council can still run; surface the cause
        # via stderr so a debugging operator sees it.
        print(
            f"::warning::list_pr_reviews got non-JSON output from gh api; "
            f"treating as cache miss. {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return []
    if not isinstance(parsed, list):
        raise RuntimeError(
            f"Expected ``gh api`` to return a JSON array of reviews, got "
            f"{type(parsed).__name__}: {str(parsed)[:200]}",
        )
    return parsed


# ---------------------------------------------------------------------------
# Body rendering
# ---------------------------------------------------------------------------


_SEVERITY_EMOJI = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🟡",
    "LOW": "🔵",
    "NIT": "⚪",
}
_CONFIDENCE_BADGE = {
    "HIGH": "verified",
    "MEDIUM": "likely",
    "LOW": "hypothetical",
}


def _format_finding(finding: Finding) -> str:
    """Render a single finding as PR-comment markdown.

    All agent-supplied text fields (``issue`` / ``suggestion`` /
    ``evidence`` / ``file``) get :func:`strip_marker_tags` applied
    before embedding — defends against the cache-poisoning vector
    where an agent might quote a PR-author-planted ``<!-- ai-review:
    <sha> -->`` from the diff. Without this strip, our own posted
    review body would carry attacker-controlled marker tags and a
    subsequent workflow run would short-circuit on the false cache
    hit. The legitimate marker is appended by ``render_review_body``
    AFTER all agent content is stripped, so only OUR tag survives.
    """
    sev = (finding.severity or "").upper()
    emoji = _SEVERITY_EMOJI.get(sev, "•")
    conf = (finding.confidence or "").upper()
    conf_badge = _CONFIDENCE_BADGE.get(conf, conf.lower() or "?")
    safe_file = strip_marker_tags(finding.file)
    location = safe_file + (f":{finding.line}" if finding.line else "")
    agent_attr = f" · _{finding.agent}_" if finding.agent else ""
    safe_issue = strip_marker_tags(finding.issue).strip()
    safe_suggestion = strip_marker_tags(finding.suggestion).strip()
    body = (
        f"#### {emoji} {sev} · _{conf_badge}_{agent_attr} — `{location}`\n"
        f"**Issue:** {safe_issue}\n\n"
        f"**Suggestion:** {safe_suggestion}"
    )
    evidence = strip_marker_tags(finding.evidence).strip()
    if evidence and evidence != "—":
        body += f"\n\n**Evidence:**\n```\n{evidence}\n```"
    return body


def _verdict_tag_value(verdict_str: str) -> str:
    """Map our ``ReviewVerdict.verdict`` to the marker's verdict vocabulary.

    The marker only knows ``approve|reject|malformed``; the council
    emits ``comment|request_changes``. ``request_changes`` is reject,
    ``comment`` is approve (we treat a clean comment as "would
    approve" — the actual GitHub approval is gated separately).
    """
    return "reject" if verdict_str == "request_changes" else "approve"


def render_review_body(
    verdict: ReviewVerdict,
    *,
    truncated: bool,
    model_label: str,
    head_sha: str,
) -> str:
    """Render the council's verdict as PR-comment markdown.

    Sections: header → scoreboard → summary → per-agent breakdown →
    findings → footer (with embedded marker comments for the
    re-review skip).
    """
    counts: dict[str, int] = {}
    for f in verdict.findings:
        sev = (f.severity or "").upper()
        counts[sev] = counts.get(sev, 0) + 1

    scoreboard_parts = [
        f"{_SEVERITY_EMOJI.get(sev, '•')} {sev}: {counts.get(sev, 0)}"
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "NIT")
        if counts.get(sev, 0) > 0
    ]
    scoreboard = " · ".join(scoreboard_parts) or "no actionable findings"

    parts: list[str] = ["## Code review council"]
    parts.append(f"**Findings:** {scoreboard}")
    if verdict.per_agent:
        per_agent_line = " · ".join(f"_{agent}_: {n}" for agent, n in verdict.per_agent.items())
        parts.append(f"**By agent:** {per_agent_line}")
    parts.append("")
    # summary is agent-generated — strip our own marker tags so an
    # agent that quotes a PR-author-planted marker can't poison the
    # cache. Triple-backtick fences are preserved (agents legitimately
    # quote code in summaries).
    parts.append(strip_marker_tags(verdict.summary).strip())

    if verdict.review_complete:
        parts.append(
            "\n> ✅ Council signalled convergence — further analysis "
            "would yield hypothetical findings only."
        )

    if truncated:
        parts.append(
            "\n> ⚠️ Diff was truncated for the model; verdict reflects the "
            "first portion of the change only."
        )

    if verdict.findings:
        parts.append("\n### Findings")
        for f in verdict.findings:
            parts.append("")
            parts.append(_format_finding(f))

    parts.append(
        f"\n---\n🤖 Reviewed by the Code Review Council "
        f"(`{model_label}`) via `.github/workflows/gemini-review.yml`."
    )
    # Markers go LAST so they stay attached even if a future renderer
    # truncates long bodies. Both tags on their own lines because some
    # markdown renderers eat adjacent HTML comments.
    parts.append("")
    parts.append(make_verdict_tag(_verdict_tag_value(verdict.verdict)))
    parts.append(make_marker(head_sha))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# GitHub plumbing
# ---------------------------------------------------------------------------


def post_review(pr_number: int, verdict: str, body: str) -> None:
    """Post the review via ``gh pr review``.

    GitHub rejects ``--approve`` / ``--request-changes`` on the
    author's own PR (HTTP 422). When that happens we fall back to
    ``--comment`` so the review body still lands — branch protection
    still sees the ``gemini-review`` check_run, which carries the
    real verdict.
    """
    flag = _VERDICT_TO_REVIEW_FLAG.get(verdict, "--comment")
    try:
        gh_run("pr", "review", str(pr_number), flag, "--body", body)
        return
    except subprocess.CalledProcessError as exc:
        if flag != "--comment":
            print(
                f"::warning::gh pr review {flag} failed "
                f"(probably author=self); falling back to --comment.\n{exc}",
                file=sys.stderr,
            )
            gh_run("pr", "review", str(pr_number), "--comment", "--body", body)
        else:
            raise


def find_stale_changes_requested_review_ids(
    reviews: list[dict[str, object]],
    *,
    bot_login: str,
) -> list[int]:
    """Return review IDs the bot should dismiss after a clean re-review.

    Filters ``reviews`` to active ``CHANGES_REQUESTED`` reviews
    submitted by ``bot_login`` (the same account this workflow runs
    as). Excludes reviews already in state ``DISMISSED`` to avoid
    re-dismissing them on each subsequent run.

    Used when the council's new verdict is ``comment`` (a clean
    re-review): GitHub's ``dismiss_stale_reviews`` branch-protection
    setting only auto-dismisses APPROVED reviews on new commits, NOT
    CHANGES_REQUESTED, so the prior reject stays stuck on the PR's
    ``reviewDecision`` until something explicitly clears it. We
    dismiss the stale reviews here so the next merge-readiness check
    sees the new verdict instead of the old one.
    """
    out: list[int] = []
    for r in reviews:
        if not isinstance(r, dict):
            continue
        if r.get("state") != "CHANGES_REQUESTED":
            continue
        user = r.get("user")
        if not isinstance(user, dict):
            continue
        if user.get("login") != bot_login:
            continue
        rid = r.get("id")
        if isinstance(rid, int):
            out.append(rid)
    return out


def dismiss_reviews(
    *,
    repo: str,
    pr_number: int,
    review_ids: list[int],
    head_sha: str,
) -> None:
    """PUT each review's dismissals endpoint with a fix-pointing message.

    Dismissal failures are warnings, not errors — branch protection
    still sees the new check_run carrying the real verdict, so a
    failed dismissal degrades us back to "human dismisses the stale
    review manually" rather than breaking the workflow. The whole
    point of this step is ergonomic; refusing to ship a clean verdict
    because dismissal failed would be the wrong trade.
    """
    message = (
        "Council re-reviewed at "
        f"`{head_sha[:7]}` — no actionable findings. "
        "Auto-dismissing the prior CHANGES_REQUESTED so "
        "``reviewDecision`` reflects the latest verdict."
    )
    for rid in review_ids:
        try:
            gh_run(
                "api",
                "--method",
                "PUT",
                f"repos/{repo}/pulls/{pr_number}/reviews/{rid}/dismissals",
                "-f",
                f"message={message}",
            )
        except subprocess.CalledProcessError as exc:
            print(
                f"::warning::Failed to dismiss stale review #{rid}; "
                f"the prior CHANGES_REQUESTED may still block merge. "
                f"Resolve via the PR UI or `gh api -X PUT "
                f"repos/{repo}/pulls/{pr_number}/reviews/{rid}/dismissals`.\n"
                f"{exc}",
                file=sys.stderr,
            )


def post_check(
    *,
    repo: str,
    head_sha: str,
    conclusion: str,
    title: str,
    summary: str,
) -> None:
    payload = {
        "name": CHECK_NAME,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "output": {"title": title, "summary": summary[:65_000]},
    }
    gh_run(
        "api",
        f"repos/{repo}/check-runs",
        "-X",
        "POST",
        "-H",
        "Accept: application/vnd.github+json",
        "--input",
        "-",
        input_data=json.dumps(payload),
    )


def post_error_check(repo: str, head_sha: str, message: str) -> None:
    post_check(
        repo=repo,
        head_sha=head_sha,
        conclusion="failure",
        title="Council review failed",
        summary=(
            f"The review workflow errored before producing a verdict.\n\n"
            f"```\n{message[:2000]}\n```\n\n"
            "Retry by pushing a new commit or rerunning the workflow."
        ),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:  # noqa: PLR0911, PLR0912, PLR0915 — each return is a distinct guard / outcome; the flow is intentionally linear so the control flow stays readable in one place
    try:
        pr_number = int(os.environ["PR_NUMBER"])
    except (KeyError, ValueError):
        print("::error::PR_NUMBER must be set to an integer", file=sys.stderr)
        return 1

    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        print("::error::GITHUB_REPOSITORY is unset", file=sys.stderr)
        return 1

    bot_login = os.environ.get("COUNCIL_BOT_LOGIN", "").strip() or DEFAULT_BOT_LOGIN

    meta = get_pr_meta(pr_number)
    head_sha = str(meta["headRefOid"])

    # Cache check — if a prior council run already produced a verdict
    # for this exact head SHA, refresh the check_run and bail. Saves
    # an LLM call per workflow re-trigger on the same commit.
    #
    # **Race-condition rejection (already considered, deliberately not
    # implemented).** Reviewers have repeatedly suggested adding a
    # post-council "re-check before posting" to defend against two
    # concurrent runs both missing the cache. That defence is BOTH
    # already provided and a non-fix:
    #
    #   1. Already provided — the workflow YAML's ``concurrency:
    #      group: gemini-review-<pr>, cancel-in-progress: true``
    #      guarantees that a second push to the same PR cancels the
    #      in-flight workflow run BEFORE this check runs. Two
    #      simultaneous council invocations against the same head
    #      SHA literally cannot start.
    #   2. A post-council re-check would NOT close the race — it
    #      would only NARROW the window. The genuine race (two runs
    #      both pass cache miss, both call the LLM, both reach
    #      "post review") still costs the LLM tokens in step 2; a
    #      re-check at step 3 saves a duplicate review-body POST
    #      but the council already ran twice. The cost of "duplicate
    #      review" is the LLM call, not the review-body POST.
    #
    # If the workflow concurrency guard is ever removed, the worst
    # case is a duplicate council run (extra LLM tokens, two near-
    # identical review bodies on the PR), not a correctness bug —
    # the marker cache hit on the NEXT trigger still works. The
    # marker tag itself IS the idempotency primitive for future
    # workflow runs; concurrent in-flight runs are the workflow-
    # scheduler's domain, not the application's.
    try:
        prior_reviews = list_pr_reviews(repo, pr_number)
    except subprocess.CalledProcessError as exc:
        # Listing reviews failed (rate limit, transient) — we'd rather
        # re-run the council than hard-fail the workflow. Skip cache
        # this time.
        print(
            f"::warning::list_pr_reviews failed; skipping marker cache check.\n{exc}",
            file=sys.stderr,
        )
        prior_reviews = []

    cached = find_cached_review(prior_reviews, head_sha=head_sha, bot_login=bot_login)
    if cached is not None:
        # ``malformed`` → ``neutral`` (not ``success``): a malformed
        # prior verdict isn't a green light, it's "we don't know what
        # the prior council decided". Unknown verdicts also default to
        # ``neutral`` so adding a new verdict value to the marker
        # vocabulary later can't silently auto-approve.
        conclusion = _CACHED_VERDICT_TO_CONCLUSION.get(cached.verdict, "neutral")
        title_map = {
            "approve": "Cached: no blocking findings (re-run)",
            "reject": "Cached: changes requested (re-run)",
            "malformed": "Cached: malformed prior verdict (re-run)",
        }
        post_check(
            repo=repo,
            head_sha=head_sha,
            conclusion=conclusion,
            title=title_map.get(cached.verdict, "Cached verdict (re-run)"),
            summary=(
                f"Council already reviewed head SHA `{head_sha}` "
                f"at {cached.submitted_at} (review #{cached.review_id}). "
                "Re-using cached verdict to save an LLM call."
            ),
        )
        return 0

    try:
        raw_diff = get_diff(pr_number)
    except subprocess.CalledProcessError as exc:
        post_error_check(repo, head_sha, f"gh pr diff failed: {exc}")
        return 1

    if not raw_diff.strip():
        post_check(
            repo=repo,
            head_sha=head_sha,
            conclusion="success",
            title="Empty diff",
            summary="No changes to review.",
        )
        return 0

    truncated = len(raw_diff) > MAX_DIFF_CHARS
    diff = raw_diff[:MAX_DIFF_CHARS] if truncated else raw_diff

    provider, model_label = _select_provider()
    council = Council(provider=provider, context=_dap_context())

    # Sanitize PR-author content before it enters the prompt — defends
    # against marker poisoning and prompt injection. The diff itself
    # is NOT sanitized (unified-diff patch lines legitimately contain
    # backticks; the agent system instruction marks the diff as data
    # explicitly).
    pr_title_clean = safe_title(str(meta.get("title") or ""))
    pr_body_clean = safe_body(meta.get("body") if isinstance(meta.get("body"), str) else None)

    try:
        verdict = council.review(
            diff=diff,
            pr_title=pr_title_clean,
            pr_body=pr_body_clean,
        )
    except Exception as exc:
        post_error_check(repo, head_sha, f"{type(exc).__name__}: {exc}")
        return 1

    body = render_review_body(
        verdict,
        truncated=truncated,
        model_label=model_label,
        head_sha=head_sha,
    )
    post_review(pr_number, verdict.verdict, body)

    # When the new verdict is ``comment`` (clean re-review with no
    # actionable findings), dismiss any prior CHANGES_REQUESTED reviews
    # from this same bot account. GitHub's ``dismiss_stale_reviews``
    # branch-protection setting only auto-dismisses APPROVED reviews on
    # new commits — CHANGES_REQUESTED stays stuck until something
    # explicitly clears it, so without this step a clean re-review
    # still leaves the PR's ``reviewDecision`` as CHANGES_REQUESTED and
    # blocks merge. ``prior_reviews`` is the same list we already
    # fetched for the marker cache check (line above), so this adds
    # one or two PUT calls per fix push, no extra reads.
    if verdict.verdict == "comment":
        stale_ids = find_stale_changes_requested_review_ids(
            prior_reviews,
            bot_login=bot_login,
        )
        if stale_ids:
            dismiss_reviews(
                repo=repo,
                pr_number=pr_number,
                review_ids=stale_ids,
                head_sha=head_sha,
            )

    # Gate fails iff the council kept a blocking finding. The arbiter
    # already filtered LOW-confidence speculation; we re-derive the
    # blocking flag from the kept list to keep this script independent
    # of the arbiter's internal state.
    blocking = any(
        (f.severity or "").upper() == "CRITICAL"
        or ((f.severity or "").upper() == "HIGH" and (f.confidence or "").upper() != "LOW")
        for f in verdict.findings
    )
    if blocking:
        conclusion = "failure"
    elif truncated:
        # Truncated diff → the council only saw a prefix. Approving
        # would be misleading ("we read the whole change"), so we
        # downshift to GitHub's ``neutral`` outcome: the check
        # completed, no blocking issue was found in what we DID see,
        # but the reviewer (or branch protection rule for required
        # checks) should treat it as a caveat rather than a green
        # light. Ported from Speecher's truncation-override pattern.
        conclusion = "neutral"
    else:
        conclusion = "success"

    severities = {(f.severity or "").upper() for f in verdict.findings}
    if blocking:
        top_sev = "CRITICAL" if "CRITICAL" in severities else "HIGH"
        title = f"Council: {top_sev} finding(s) require changes"
    elif truncated:
        title = f"Council: reviewed first {MAX_DIFF_CHARS:_} of {len(raw_diff):_} diff chars"
    elif verdict.findings:
        title = f"Council: {len(verdict.findings)} non-blocking finding(s)"
    elif verdict.review_complete:
        title = "Council: review complete — no actionable findings"
    else:
        title = "Council: no findings reported"

    post_check(
        repo=repo,
        head_sha=head_sha,
        conclusion=conclusion,
        title=title,
        summary=verdict.summary[:2000],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
