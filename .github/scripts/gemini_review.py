"""Post a Gemini-generated review on the current PR + a ``gemini-review`` check_run.

Triggered by ``.github/workflows/gemini-review.yml`` on pull_request events.
Reads the diff via ``gh pr diff``, asks Gemini for a structured verdict,
posts the verdict back as a real PR review (``gh pr review``), and records
the same outcome on a synthetic ``gemini-review`` check_run so branch
protection has something to gate on.

The reviewer prompt lives in ``.github/prompts/code-review.md`` (tracked;
CI uses this) with an optional operator-local override at
``.claude/prompts/code-review.md`` (gitignored; wins when present so
developers can iterate without committing). The anti-hallucination
knobs — ``confidence`` field on every finding, LOW-confidence drop,
explicit out-of-scope list, ``review_complete`` stop flag — are
documented in the prompt itself rather than baked into this script.

Environment:
    GEMINI_API_KEY      Required. Google AI Studio key. Free tier handles
                         ~250 PR reviews/day on gemini-2.5-flash.
    GH_TOKEN            Required. The workflow's ``GITHUB_TOKEN``, scoped to
                         ``pull-requests: write`` + ``checks: write``.
    GITHUB_REPOSITORY   Auto-injected by GitHub Actions (``owner/repo``).
    PR_NUMBER           Required. Plumbed from the workflow event.
    GEMINI_MODEL        Optional. Defaults to ``gemini-2.5-flash``;
                         override via repo variable to e.g.
                         ``gemini-3-pro-preview`` for deeper analysis.

Design notes:
    - SDK is ``google-genai`` (the current package), not the deprecated
      ``google-generativeai``. Structured output via Pydantic-based
      JSON schema.
    - Diff truncated at 60k chars to stay inside AI Studio's tokens-per-
      minute limits; truncation flagged in the posted review.
    - Findings with ``confidence: LOW`` are dropped before posting unless
      their severity is NIT — the main anti-hallucination filter.
    - The gate fails iff a kept finding is CRITICAL, or HIGH-severity
      with non-LOW confidence. ``review_complete: true`` from the model
      signals convergence (no further actionable findings expected).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

CHECK_NAME = "gemini-review"
DEFAULT_MODEL = "gemini-2.5-flash"
# Canonical prompt location — tracked in the repo so CI always has it.
# This is the file that ships with the workflow; edits here flow to
# every PR review on next push. ``.github/`` (not ``.claude/``) is the
# right home because ``.claude/`` is gitignored as operator-only tooling.
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "code-review.md"
# Optional operator override — if present locally under ``.claude/``,
# wins over the in-repo prompt. Gitignored, so this is per-developer
# scratch space for iterating on review tone without touching the
# tracked file. CI never sees it (the dir isn't checked out).
LOCAL_OVERRIDE_PROMPT = (
    Path(__file__).resolve().parents[2] / ".claude" / "prompts" / "code-review.md"
)
MAX_DIFF_CHARS = 60_000
MAX_BODY_CHARS = 4_000


class Finding(BaseModel):
    """A single review observation.

    The schema is rigid by design — every field is required so vague
    findings don't slip through. The ``confidence`` field is the
    main anti-hallucination lever: LOW-confidence findings get dropped
    (or downgraded to NIT) by the post-processor before they ever
    reach the PR review.
    """

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
    confidence: str = Field(
        description=(
            "One of 'HIGH', 'MEDIUM', 'LOW'. "
            "HIGH = verified in this code (the issue is concretely "
            "present at the cited file:line). "
            "MEDIUM = likely an issue, supported by the surrounding "
            "code but requires a small leap. "
            "LOW = hypothetical — 'what if a user did X' or 'in "
            "scenario Y'. LOW-confidence findings are auto-dropped "
            "by the post-processor unless severity is also NIT."
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
    evidence: str = Field(
        default="",
        description=(
            "Verbatim quote of the problematic code from the diff. "
            "Helps the author verify the model isn't hallucinating a "
            "line that doesn't exist. Use '—' for cross-file findings "
            "where no single line applies."
        ),
    )


class ReviewVerdict(BaseModel):
    """Structured output schema for Gemini's review.

    ``approve`` is intentionally not a permitted verdict — strict mode
    never rubber-stamps a diff. The worst the reviewer can do for a
    clean change is post a ``comment`` review with ``review_complete``
    set true and an empty ``findings`` array; the check_run gate still
    passes because no CRITICAL/HIGH finding is present.
    """

    verdict: str = Field(
        description=(
            "One of 'request_changes', 'comment'. "
            "Use 'request_changes' iff there is at least one CRITICAL "
            "or HIGH-confidence HIGH-severity finding in the findings "
            "array. Use 'comment' otherwise — even when there are "
            "MEDIUM / LOW / NIT findings. Never 'approve'."
        ),
    )
    summary: str = Field(
        description=(
            "Plain-prose take on the change. Open with the most "
            "important observation — not 'looks good' or 'well done'. "
            "If genuinely nothing is worth flagging, lead with "
            "'no critical issues found after analysis' and set "
            "``review_complete`` true."
        ),
    )
    findings: list[Finding] = Field(
        default_factory=list,
        description=(
            "List of issues. **Empty list is permitted** when no real "
            "issue exists — quality over quantity. Don't pad with "
            "hypotheticals to look thorough. Sort CRITICAL → NIT."
        ),
    )
    review_complete: bool = Field(
        default=False,
        description=(
            "Set true when the reviewer hit the stop condition: "
            "remaining findings would be hypothetical, defensive nits, "
            "or recurring observations from earlier rounds. Used by "
            "the check_run renderer to communicate convergence in the "
            "title (e.g. 'review complete' vs 'N findings open')."
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


_FALLBACK_SYSTEM_INSTRUCTION = (
    "You are a senior code reviewer for the DAP repo. Find HIGH-SIGNAL "
    "issues, not volume. Quality > quantity. Empty findings is fine "
    "if no real issue exists. Avoid hypotheticals, defensive nits on "
    "framework defaults, and out-of-scope concerns (i18n, CSP, RTL — "
    "DAP doesn't use them). Severity NIT is the floor for "
    "LOW-confidence findings. Open the summary with the most important "
    "observation, not flattery."
)


def _load_system_instruction() -> str:
    """Load the prompt — operator override first, then canonical, then fallback.

    Order matters: a developer iterating on tone via the gitignored
    ``.claude/prompts/code-review.md`` should see their changes
    without committing. CI never has that file, so it always uses
    the tracked ``.github/prompts/code-review.md``.
    """
    for candidate in (LOCAL_OVERRIDE_PROMPT, PROMPT_PATH):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    print(
        f"::warning::Prompt file not found at {LOCAL_OVERRIDE_PROMPT} "
        f"or {PROMPT_PATH}; falling back to inline minimal prompt.",
        file=sys.stderr,
    )
    return _FALLBACK_SYSTEM_INSTRUCTION


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
    system_instruction = _load_system_instruction()

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
_CONFIDENCE_BADGE = {
    "HIGH": "verified",
    "MEDIUM": "likely",
    "LOW": "hypothetical",
}


def _sort_findings(findings: list[Finding]) -> list[Finding]:
    """Stable sort CRITICAL → HIGH → MEDIUM → LOW → NIT.

    Unknown severities (model hallucinated a label) sink to the bottom
    so they're visible but don't crowd the real issues.
    """
    return sorted(findings, key=lambda f: _SEVERITY_ORDER.get(f.severity.upper(), 99))


def _filter_low_confidence(findings: list[Finding]) -> tuple[list[Finding], int]:
    """Drop LOW-confidence findings unless they're already at NIT severity.

    The prompt instructs the model to do this itself, but a belt-and-
    braces filter here means we never ship a hypothetical-but-MEDIUM
    finding even if the model slips. Returns the kept list plus the
    count of dropped items so the renderer can mention it in the body.
    """
    kept: list[Finding] = []
    dropped = 0
    for f in findings:
        conf = (f.confidence or "").upper()
        sev = (f.severity or "").upper()
        if conf == "LOW" and sev != "NIT":
            dropped += 1
            continue
        kept.append(f)
    return kept, dropped


def _format_finding(finding: Finding) -> str:
    sev = (finding.severity or "").upper()
    emoji = _SEVERITY_EMOJI.get(sev, "•")
    conf = (finding.confidence or "").upper()
    conf_badge = _CONFIDENCE_BADGE.get(conf, conf.lower() or "?")
    location = finding.file + (f":{finding.line}" if finding.line else "")
    body = (
        f"#### {emoji} {sev} · _{conf_badge}_ — `{location}`\n"
        f"**Issue:** {finding.issue.strip()}\n\n"
        f"**Suggestion:** {finding.suggestion.strip()}"
    )
    evidence = (finding.evidence or "").strip()
    if evidence and evidence != "—":
        body += f"\n\n**Evidence:**\n```\n{evidence}\n```"
    return body


def render_review_body(
    verdict: ReviewVerdict,
    *,
    truncated: bool,
    model: str,
) -> tuple[str, list[Finding]]:
    """Render the review as PR-comment markdown + return filtered findings.

    Returns ``(body, kept_findings)`` so ``main()`` can use the same
    filtered list for the check_run conclusion (LOW-confidence dropouts
    don't count toward request_changes).

    Layout: header, scoreboard, summary, then each finding as its own
    H4 section. Truncation and review-complete banners go above the
    findings so the reader sees them first.
    """
    sorted_findings = _sort_findings(verdict.findings)
    kept, dropped = _filter_low_confidence(sorted_findings)

    counts: dict[str, int] = {}
    for f in kept:
        sev = (f.severity or "").upper()
        counts[sev] = counts.get(sev, 0) + 1

    scoreboard_parts = [
        f"{_SEVERITY_EMOJI.get(sev, '•')} {sev}: {counts.get(sev, 0)}"
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "NIT")
        if counts.get(sev, 0) > 0
    ]
    scoreboard = " · ".join(scoreboard_parts) or "no actionable findings"

    parts: list[str] = ["## Gemini code review"]
    parts.append(f"**Findings:** {scoreboard}")
    if dropped:
        parts.append(
            f"_(filtered out {dropped} LOW-confidence findings — see "
            "`.github/prompts/code-review.md` for the anti-hallucination "
            "rules)_"
        )
    parts.append("")
    parts.append(verdict.summary.strip())

    if verdict.review_complete:
        parts.append(
            "\n> ✅ Reviewer signalled convergence — further analysis "
            "would yield hypothetical findings only."
        )

    if truncated:
        parts.append(
            "\n> ⚠️ Diff was truncated for the model; verdict reflects the "
            "first portion of the change only — findings may not cover "
            "the tail of the diff."
        )

    if kept:
        parts.append("\n### Findings")
        for f in kept:
            parts.append("")
            parts.append(_format_finding(f))

    parts.append(
        f"\n---\n🤖 Reviewed by `{model}` via "
        "`.github/workflows/gemini-review.yml` "
        "(prompt: `.github/prompts/code-review.md`)."
    )
    return "\n".join(parts), kept


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

    body, kept = render_review_body(verdict, truncated=truncated, model=model)
    post_review(pr_number, verdict.verdict, body)

    # Gate fails iff a kept finding (post LOW-confidence filter) is
    # either CRITICAL, or HIGH-severity with HIGH/MEDIUM confidence.
    # HIGH-severity LOW-confidence wouldn't have survived the filter,
    # but the explicit check here documents the intent.
    blocking = any(
        (f.severity or "").upper() == "CRITICAL"
        or ((f.severity or "").upper() == "HIGH" and (f.confidence or "").upper() != "LOW")
        for f in kept
    )
    conclusion = "failure" if blocking else "success"

    severities = {(f.severity or "").upper() for f in kept}
    if blocking:
        top_sev = "CRITICAL" if "CRITICAL" in severities else "HIGH"
        title = f"Gemini: {top_sev} finding(s) require changes"
    elif kept:
        title = f"Gemini: {len(kept)} non-blocking finding(s)"
    elif verdict.review_complete:
        title = "Gemini: review complete — no actionable findings"
    else:
        title = "Gemini: no findings reported"

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
