"""Drive the Code Review Council against a PR + post the result back.

Thin adapter between GitHub Actions and the
:mod:`code_review_council` package. Reads the diff via ``gh pr diff``,
builds a ``ProjectContext`` tailored to DAP, runs the Council, and
posts the verdict as a real PR review plus a ``gemini-review``
check_run so branch protection has something to gate on.

Environment:
    GEMINI_API_KEY      Required. Google AI Studio key.
    GH_TOKEN            Required. Workflow ``GITHUB_TOKEN`` scoped to
                         ``pull-requests: write`` + ``checks: write``.
    GITHUB_REPOSITORY   Auto-injected — ``owner/repo``.
    PR_NUMBER           Required. Plumbed from the workflow event.
    GEMINI_MODEL        Optional. Defaults to ``gemini-2.5-flash``;
                         override to ``gemini-3-pro-preview`` for
                         deeper analysis (HIGH thinking auto-enabled).

The reviewer prompts live INSIDE the council package's agent
classes — see ``packages/code-review-council/src/code_review_council/
agents/{security,correctness}.py``. To tweak tone/scope edit those
files (or add a new agent) rather than reaching into this script.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from code_review_council import Council, Finding, ProjectContext, ReviewVerdict
from code_review_council.providers import GeminiProvider

CHECK_NAME = "gemini-review"
DEFAULT_MODEL = "gemini-2.5-flash"
MAX_DIFF_CHARS = 60_000
MAX_BODY_CHARS = 4_000


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
    sev = (finding.severity or "").upper()
    emoji = _SEVERITY_EMOJI.get(sev, "•")
    conf = (finding.confidence or "").upper()
    conf_badge = _CONFIDENCE_BADGE.get(conf, conf.lower() or "?")
    location = finding.file + (f":{finding.line}" if finding.line else "")
    agent_attr = f" · _{finding.agent}_" if finding.agent else ""
    body = (
        f"#### {emoji} {sev} · _{conf_badge}_{agent_attr} — `{location}`\n"
        f"**Issue:** {finding.issue.strip()}\n\n"
        f"**Suggestion:** {finding.suggestion.strip()}"
    )
    evidence = (finding.evidence or "").strip()
    if evidence and evidence != "—":
        body += f"\n\n**Evidence:**\n```\n{evidence}\n```"
    return body


def render_review_body(verdict: ReviewVerdict, *, truncated: bool, model: str) -> str:
    """Render the council's verdict as PR-comment markdown.

    Sections: header → scoreboard → summary → per-agent breakdown →
    findings → footer. The per-agent line lets readers triage by
    specialist without scanning every finding.
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

    parts: list[str] = ["## Gemini code review (council)"]
    parts.append(f"**Findings:** {scoreboard}")
    if verdict.per_agent:
        per_agent_line = " · ".join(f"_{agent}_: {n}" for agent, n in verdict.per_agent.items())
        parts.append(f"**By agent:** {per_agent_line}")
    parts.append("")
    parts.append(verdict.summary.strip())

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
        f"(`{model}`) via `.github/workflows/gemini-review.yml`. "
        "Agents: Security + Correctness."
    )
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
    flag_map = {
        "request_changes": "--request-changes",
        "comment": "--comment",
    }
    flag = flag_map.get(verdict, "--comment")
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
        title="Gemini review failed",
        summary=(
            f"The review workflow errored before producing a verdict.\n\n"
            f"```\n{message[:2000]}\n```\n\n"
            "Retry by pushing a new commit or rerunning the workflow."
        ),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:  # noqa: PLR0911 — each return is a distinct guard / outcome
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("::error::GEMINI_API_KEY is unset", file=sys.stderr)
        return 1

    try:
        pr_number = int(os.environ["PR_NUMBER"])
    except (KeyError, ValueError):
        print("::error::PR_NUMBER must be set to an integer", file=sys.stderr)
        return 1

    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        print("::error::GITHUB_REPOSITORY is unset", file=sys.stderr)
        return 1

    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    meta = get_pr_meta(pr_number)
    head_sha = str(meta["headRefOid"])

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

    provider = GeminiProvider(api_key=api_key, model=model)
    council = Council(provider=provider, context=_dap_context())

    pr_body_raw = meta.get("body") or ""
    pr_body = str(pr_body_raw)[:MAX_BODY_CHARS]

    try:
        verdict = council.review(
            diff=diff,
            pr_title=str(meta.get("title") or ""),
            pr_body=pr_body,
        )
    except Exception as exc:
        post_error_check(repo, head_sha, f"{type(exc).__name__}: {exc}")
        return 1

    body = render_review_body(verdict, truncated=truncated, model=model)
    post_review(pr_number, verdict.verdict, body)

    # Gate fails iff the council kept a blocking finding. The arbiter
    # already filtered LOW-confidence speculation; we re-derive the
    # blocking flag from the kept list to keep this script independent
    # of the arbiter's internal state.
    blocking = any(
        (f.severity or "").upper() == "CRITICAL"
        or ((f.severity or "").upper() == "HIGH" and (f.confidence or "").upper() != "LOW")
        for f in verdict.findings
    )
    conclusion = "failure" if blocking else "success"

    severities = {(f.severity or "").upper() for f in verdict.findings}
    if blocking:
        top_sev = "CRITICAL" if "CRITICAL" in severities else "HIGH"
        title = f"Council: {top_sev} finding(s) require changes"
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
