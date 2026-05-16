"""Post a Gemini-generated review on the current PR + a ``gemini-review`` check_run.

Triggered by ``.github/workflows/gemini-review.yml`` on pull_request events.
Reads the diff via ``gh pr diff``, asks Gemini for a structured verdict,
posts the verdict back as a real PR review (``gh pr review``), and records
the same outcome on a synthetic ``gemini-review`` check_run so branch
protection has something to gate on.

Environment:
    GEMINI_API_KEY      Required. Google AI Studio key. Free tier handles
                         ~250 PR reviews/day on gemini-2.5-flash.
    GH_TOKEN            Required. The workflow's ``GITHUB_TOKEN``, scoped to
                         ``pull-requests: write`` + ``checks: write``.
    GITHUB_REPOSITORY   Auto-injected by GitHub Actions (``owner/repo``).
    PR_NUMBER           Required. Plumbed from the workflow event.
    GEMINI_MODEL        Optional. Defaults to ``gemini-2.5-flash``.

Design notes:
    - We use the **Google Gen AI Python SDK** (``google-genai``), not the
      deprecated ``google-generativeai``. The SDK exposes structured
      output via ``response_mime_type='application/json'`` +
      ``response_json_schema=<pydantic model class or dict>``.
    - The diff is truncated at 60k characters. ``gemini-2.5-flash`` has a
      1M-token context window but the AI Studio free tier rate-limits on
      tokens-per-minute, so we cap input. Truncation is flagged in the
      posted review so the operator knows the verdict is partial.
    - The review verdict maps onto ``gh pr review --approve|--request-changes
      |--comment``. Branch protection sees the resulting ``gemini-review``
      check as ``success`` on ``approve``/``comment`` and ``failure`` on
      ``request-changes``.
    - Idempotency: posting two reviews on the same SHA is fine
      (GitHub allows it); each appears as a fresh review timeline entry.
      The check_run is posted as a new entry each run; the *latest* one
      wins for branch protection. No de-dupe needed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

CHECK_NAME = "gemini-review"
DEFAULT_MODEL = "gemini-2.5-flash"
MAX_DIFF_CHARS = 60_000
MAX_BODY_CHARS = 4_000


class Finding(BaseModel):
    """A single review observation. The schema is intentionally rigid —
    the strict prompt instructs the model to populate every field; vague
    findings without a file/line citation are explicitly disallowed."""

    severity: str = Field(
        description=(
            "One of 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'NIT'. "
            "CRITICAL = data loss, security hole, broken contract. "
            "HIGH = real bug or design flaw that should block merge. "
            "MEDIUM = correctness concern or missing edge case. "
            "LOW = code-quality / maintainability issue. "
            "NIT = style or naming opinion."
        ),
    )
    file: str = Field(
        description=(
            "Path to the file the finding applies to, relative to repo "
            "root. Mandatory — a finding without a file path is useless "
            "for the author."
        ),
    )
    line: int | None = Field(
        default=None,
        description=(
            "Line number in the new (post-diff) version of the file. "
            "Omit only when the issue is genuinely cross-file (e.g. "
            "'no test coverage in tests/ for this new module')."
        ),
    )
    issue: str = Field(
        description=(
            "Concrete description of the problem. State what's wrong, "
            "not what was changed. One-to-three sentences."
        ),
    )
    suggestion: str = Field(
        description=(
            "Concrete remediation: what to do to fix the issue. Code "
            "snippet welcome but not required. One-to-three sentences."
        ),
    )


class ReviewVerdict(BaseModel):
    """Structured output schema for Gemini's review.

    Note: ``approve`` is intentionally *not* a permitted verdict. Strict-
    reviewer mode means the reviewer's job is to find issues, not to
    pat the author on the back; the worst the reviewer can do for a
    clean diff is post a ``comment`` review with a ``no_critical_issues``
    flag set true. Branch protection still gets satisfied because the
    ``gemini-review`` check_run reports ``success`` whenever no
    CRITICAL/HIGH finding is present.
    """

    verdict: str = Field(
        description=(
            "One of 'request_changes', 'comment'. "
            "Use 'request_changes' iff there is at least one CRITICAL "
            "or HIGH finding in the findings array. "
            "Use 'comment' otherwise — even when there are MEDIUM / "
            "LOW / NIT findings. Never 'approve'; strict-reviewer mode "
            "doesn't reward clean diffs with an approval."
        ),
    )
    summary: str = Field(
        description=(
            "Two-to-four sentence high-level take on the change: what "
            "it accomplishes and the most pressing concerns. Don't "
            "say 'looks good' or 'well done'. If genuinely nothing is "
            "worth flagging, write 'no critical issues found after "
            "exhaustive analysis' verbatim."
        ),
    )
    findings: list[Finding] = Field(
        default_factory=list,
        description=(
            "List of issues sorted CRITICAL → NIT. The reviewer must "
            "report at minimum three findings *per touched file* unless "
            "the touched file is genuinely tiny (under ~10 lines of "
            "real change). If after exhaustive analysis no CRITICAL/HIGH "
            "issue exists, list NITs and explicitly set "
            "``no_critical_issues`` true; the array must still contain "
            "concrete observations, never be empty."
        ),
    )
    no_critical_issues: bool = Field(
        default=False,
        description=(
            "Set true only after exhaustive analysis confirmed there "
            "are no CRITICAL or HIGH findings. False when at least "
            "one CRITICAL/HIGH was flagged. Used by the workflow to "
            "decide the gemini-review check_run conclusion."
        ),
    )


def gh_capture(*args: str) -> str:
    """Run gh CLI and return stdout, raising on non-zero exit."""
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def gh_run(*args: str, input_data: str | None = None) -> None:
    """Run gh CLI for side effects; raises on non-zero exit."""
    subprocess.run(
        ["gh", *args],
        input=input_data,
        text=True,
        check=True,
    )


def get_pr_meta(pr_number: int) -> dict[str, Any]:
    payload = gh_capture(
        "pr",
        "view",
        str(pr_number),
        "--json",
        "title,body,headRefOid,additions,deletions,changedFiles",
    )
    data: dict[str, Any] = json.loads(payload)
    return data


def get_diff(pr_number: int) -> str:
    return gh_capture("pr", "diff", str(pr_number))


def call_gemini(
    *,
    api_key: str,
    model: str,
    pr_title: str,
    pr_body: str,
    diff: str,
    truncated: bool,
) -> ReviewVerdict:
    client = genai.Client(api_key=api_key)

    system_instruction = (
        # ── Persona ──────────────────────────────────────────────
        "You are a STRICT senior code reviewer with 20 years of "
        "experience reviewing Python (FastAPI / SQLAlchemy / LangGraph) "
        "and TypeScript (Next.js / React) codebases. Your job is to "
        "FIND PROBLEMS, not to approve code. Reviewers are valuable "
        "in proportion to the issues they catch — be that reviewer.\n\n"
        # ── Repo context ─────────────────────────────────────────
        "Repository: DAP — a self-hosted pipeline orchestration engine. "
        "Monorepo with apps/engine (FastAPI), apps/dashboard (Next.js), "
        "packages/{types,runtimes,prompt-dsl,schemas}, tests/smoke "
        "(pytest), e2e (Playwright). House conventions that matter:\n"
        "- Python: type hints required everywhere. ``Any`` in route "
        "signatures is a smell — prefer the concrete pydantic model "
        "or dataclass. mypy strict and ruff format/check must pass.\n"
        "- API: admin endpoints return 404 (not 403) for non-admins "
        "as anti-enumeration. Use ``require_admin_user`` dep, not a "
        "hand-rolled ``if not user.is_superuser`` check.\n"
        "- Audit events go through ``record_audit_event`` typed with "
        "the ``AuditEventType`` Literal — string literals are a smell.\n"
        "- Tests live in ``tests/smoke/``. ``client`` / ``authed_client`` "
        "fixtures come from ``tests/smoke/conftest.py``; new tests "
        "should not re-roll the fixture locally.\n\n"
        # ── Review rules ─────────────────────────────────────────
        "Mandatory analysis steps for EVERY file touched by the diff:\n"
        "1. Identify at least 3 plausible issues. Bugs, edge cases, "
        "security, performance, missing tests, race conditions, "
        "concurrency. If the file is genuinely under ~10 lines of "
        "real change you may produce fewer; otherwise three is the "
        "minimum.\n"
        "2. Question every assumption the code makes. What if the "
        "input is None? Empty? Whitespace-only? Unicode-pathological? "
        "Already locked? Already deleted? Race-conditioned?\n"
        "3. Flag explicitly when these are MISSING: error handling, "
        "input validation, tests for the new behaviour, edge-case "
        "tests, null/empty-collection checks, type narrowing, "
        "transactional boundaries, audit trail.\n"
        "4. Every finding cites file:line. No line ⇒ explicitly say "
        "'no specific line — cross-file concern' in the issue text.\n"
        "5. Severity is honest. CRITICAL = data loss / security hole / "
        "broken contract. HIGH = real bug or design flaw that blocks "
        "merge. MEDIUM = correctness concern. LOW = quality issue. "
        "NIT = style / naming. If unsure, downgrade.\n\n"
        # ── Anti-flattery / output style ─────────────────────────
        "ABSOLUTELY DO NOT say 'looks good', 'well done', 'great job', "
        "'nicely refactored', or any flattery. Your value is in finding "
        "problems. If after exhaustive analysis you genuinely find no "
        "CRITICAL or HIGH issues, list the NITs and set the JSON field "
        "``no_critical_issues`` to true — and the summary MUST be the "
        "verbatim phrase 'no critical issues found after exhaustive "
        "analysis'. Approval is not a permitted verdict in strict mode.\n\n"
        "Output: the structured JSON schema you've been given. The "
        "``findings`` array is NEVER empty — even a clean refactor "
        "yields at minimum a NIT or a 'missing test' observation. "
        "Sort findings CRITICAL → HIGH → MEDIUM → LOW → NIT."
    )

    user_content = f"PR title: {pr_title}\n\nPR description:\n{pr_body[:MAX_BODY_CHARS]}\n\n"
    if truncated:
        user_content += (
            f"NOTE: the diff was truncated to {MAX_DIFF_CHARS} characters "
            "(too large for a single review). Verdict is based on the "
            "visible portion only.\n\n"
        )
    user_content += f"Diff:\n```diff\n{diff}\n```"

    # Build the config. For Gemini 3.x models, opt into HIGH thinking
    # level so the strict-reviewer prompt actually triggers deep analysis
    # instead of skimming the diff. Older models silently ignore
    # ``thinking_config``; the SDK rejects it with a 400 for them, so
    # we gate the flag on the model name.
    config_kwargs: dict[str, Any] = dict(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_json_schema=ReviewVerdict.model_json_schema(),
        temperature=0.2,
    )
    if model.startswith("gemini-3"):
        config_kwargs["thinking_config"] = types.ThinkingConfig(
            thinking_level=types.ThinkingLevel.HIGH,
        )

    response = client.models.generate_content(
        model=model,
        contents=user_content,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    if not response.text:
        raise RuntimeError("Gemini returned an empty response body")
    return ReviewVerdict.model_validate_json(response.text)


_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "NIT": 4}
_SEVERITY_EMOJI = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🟡",
    "LOW": "🔵",
    "NIT": "⚪",
}


def _sort_findings(findings: list[Finding]) -> list[Finding]:
    """Stable sort CRITICAL → HIGH → MEDIUM → LOW → NIT.

    Unknown severities (model hallucinated a label) sink to the bottom
    so they're visible but don't crowd the real issues.
    """
    return sorted(findings, key=lambda f: _SEVERITY_ORDER.get(f.severity.upper(), 99))


def _format_finding(finding: Finding) -> str:
    sev = finding.severity.upper()
    emoji = _SEVERITY_EMOJI.get(sev, "•")
    location = finding.file + (f":{finding.line}" if finding.line else "")
    return (
        f"#### {emoji} {sev} — `{location}`\n"
        f"**Issue:** {finding.issue.strip()}\n\n"
        f"**Suggestion:** {finding.suggestion.strip()}"
    )


def render_review_body(verdict: ReviewVerdict, *, truncated: bool, model: str) -> str:
    """Render the strict-reviewer findings as PR-comment markdown.

    Layout: header, one-line scoreboard of severity counts, the summary,
    then each finding as its own H4 section so reviewers can jump
    between them via the GitHub TOC. Truncation banner appears before
    the findings so the reader knows the analysis is partial.
    """
    findings = _sort_findings(verdict.findings)

    counts: dict[str, int] = {}
    for f in findings:
        sev = f.severity.upper()
        counts[sev] = counts.get(sev, 0) + 1

    scoreboard_parts = [
        f"{_SEVERITY_EMOJI.get(sev, '•')} {sev}: {counts.get(sev, 0)}"
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "NIT")
        if counts.get(sev, 0) > 0
    ]
    scoreboard = " · ".join(scoreboard_parts) or "no findings reported"

    parts: list[str] = ["## Gemini code review (strict mode)"]
    parts.append(f"**Findings:** {scoreboard}")
    parts.append("")
    parts.append(verdict.summary.strip())

    if truncated:
        parts.append(
            "\n> ⚠️ Diff was truncated for the model; verdict reflects the "
            "first portion of the change only — findings may not cover "
            "the tail of the diff."
        )

    if findings:
        parts.append("\n### Findings")
        for f in findings:
            parts.append("")
            parts.append(_format_finding(f))
    else:
        # Schema says findings is never empty; if the model returned an
        # empty list anyway, flag the breach so the operator can spot
        # prompt drift.
        parts.append(
            "\n> ⚠️ Model returned no findings despite the strict prompt — "
            "this likely means the prompt drifted or the model fell out "
            "of structured-output mode. Investigate the workflow log."
        )

    parts.append(
        f"\n---\n🤖 Reviewed by `{model}` "
        "(strict reviewer mode) via `.github/workflows/gemini-review.yml`."
    )
    return "\n".join(parts)


def post_review(pr_number: int, verdict: str, body: str) -> None:
    """Post the review via ``gh pr review``.

    Note: gh's ``--approve`` cannot be used by the PR author on their own
    PR (GitHub rejects with HTTP 422). When the workflow author and PR
    author are the same account (single-maintainer repo, common case
    here) we fall back to ``--comment`` and surface the original
    verdict in the body text — branch protection still sees the
    ``gemini-review`` check, which carries the real verdict.
    """
    # Strict mode: ``approve`` is not a permitted verdict in the schema,
    # so we only ever post request-changes or comment here. ``approve``
    # left in the map only as a defensive fallback for prompt drift.
    flag_map = {
        "approve": "--comment",  # strict mode rewrites approve → comment
        "request_changes": "--request-changes",
        "comment": "--comment",
    }
    flag = flag_map.get(verdict, "--comment")
    try:
        gh_run("pr", "review", str(pr_number), flag, "--body", body)
        return
    except subprocess.CalledProcessError as exc:
        # GitHub rejects approve / request-changes on the author's own PR.
        # Retry as a comment so the review still lands.
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
    # ``gh api ... --input -`` reads JSON from stdin → no shell quoting drama.
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

    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)

    meta = get_pr_meta(pr_number)
    head_sha = meta["headRefOid"]

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

    try:
        verdict = call_gemini(
            api_key=api_key,
            model=model,
            pr_title=meta.get("title") or "",
            pr_body=meta.get("body") or "",
            diff=diff,
            truncated=truncated,
        )
    except Exception as exc:
        post_error_check(repo, head_sha, f"{type(exc).__name__}: {exc}")
        return 1

    body = render_review_body(verdict, truncated=truncated, model=model)
    post_review(pr_number, verdict.verdict, body)

    # The gate fails iff there's at least one CRITICAL or HIGH finding.
    # We trust the model's ``no_critical_issues`` flag but also re-check
    # the findings array — if the model set the flag but a HIGH/CRITICAL
    # finding leaked through, the findings array wins (belt-and-braces).
    severities = {f.severity.upper() for f in verdict.findings}
    blocking = bool(severities & {"CRITICAL", "HIGH"})
    conclusion = "failure" if blocking else "success"

    # Title summarises the top finding's severity so branch protection's
    # check listing communicates triage priority at a glance.
    if blocking:
        top_sev = "CRITICAL" if "CRITICAL" in severities else "HIGH"
        title = f"Gemini strict: {top_sev} finding(s) require changes"
    elif verdict.findings:
        title = f"Gemini strict: {len(verdict.findings)} non-blocking finding(s)"
    else:
        title = "Gemini strict: no findings reported"

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
