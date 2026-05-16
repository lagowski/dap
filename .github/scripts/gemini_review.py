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


class ReviewVerdict(BaseModel):
    """Structured output schema for Gemini's review."""

    verdict: str = Field(
        description=(
            "One of 'approve', 'request_changes', 'comment'. "
            "Use 'approve' for diffs that look ready to merge. "
            "Use 'request_changes' only for findings the author must "
            "address before merge (bugs, security issues, broken tests). "
            "Use 'comment' for non-blocking observations or when the "
            "diff is too small/unclear to give a strong verdict."
        ),
    )
    summary: str = Field(
        description=(
            "1-3 paragraph high-level take on the change. What does it "
            "accomplish, is the approach sound, any architectural "
            "concerns? Plain prose, no bullet lists."
        ),
    )
    concerns: list[str] = Field(
        default_factory=list,
        description=(
            "Specific issues the author should know about — bugs, edge "
            "cases missed, design smells. Each entry is one sentence. "
            "Empty list if no concerns. Don't pad with nitpicks."
        ),
    )
    praise: list[str] = Field(
        default_factory=list,
        description=(
            "Things the change did well — clean refactors, good test "
            "coverage, careful documentation. Each entry is one "
            "sentence. Empty list if nothing notable. Don't manufacture "
            "praise; an empty list is fine."
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
        "You are a senior code reviewer for the DAP repository — a "
        "self-hosted pipeline orchestration engine (Python / FastAPI + "
        "LangGraph + Next.js dashboard, monorepo with apps/engine, "
        "apps/dashboard, packages/{types,runtimes,prompt-dsl,schemas}).\n\n"
        "House conventions you should hold the change to:\n"
        "- Python: type hints required everywhere; avoid ``Any`` in route "
        "signatures (prefer the concrete dataclass / pydantic model).\n"
        "- API: admin-only endpoints return 404 (not 403) for non-admins "
        "as an anti-enumeration measure. Use the ``require_admin_user`` dep.\n"
        "- Audit events go through ``record_audit_event`` typed with the "
        "``AuditEventType`` Literal.\n"
        "- Tests live in ``tests/smoke/``. The ``client`` and ``authed_client`` "
        "fixtures come from ``tests/smoke/conftest.py``; new tests should "
        "reuse them rather than rolling a local fixture.\n"
        "- mypy strict + ruff format/check must pass.\n\n"
        "Style of your review: terse, factual, no flattery. Use 'request_changes' "
        "only when the diff has a genuine bug or contract violation the author "
        "must fix; otherwise prefer 'approve' or 'comment'. Don't manufacture "
        "concerns to look thorough — an empty 'concerns' list is the right "
        "answer for a clean refactor."
    )

    user_content = f"PR title: {pr_title}\n\nPR description:\n{pr_body[:MAX_BODY_CHARS]}\n\n"
    if truncated:
        user_content += (
            f"NOTE: the diff was truncated to {MAX_DIFF_CHARS} characters "
            "(too large for a single review). Verdict is based on the "
            "visible portion only.\n\n"
        )
    user_content += f"Diff:\n```diff\n{diff}\n```"

    response = client.models.generate_content(
        model=model,
        contents=user_content,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_json_schema=ReviewVerdict.model_json_schema(),
            temperature=0.2,
        ),
    )
    if not response.text:
        raise RuntimeError("Gemini returned an empty response body")
    return ReviewVerdict.model_validate_json(response.text)


def render_review_body(verdict: ReviewVerdict, *, truncated: bool, model: str) -> str:
    parts: list[str] = []
    parts.append("## Gemini code review\n")
    parts.append(verdict.summary.strip())

    if verdict.concerns:
        parts.append("\n### Concerns")
        parts.extend(f"- {item.strip()}" for item in verdict.concerns)

    if verdict.praise:
        parts.append("\n### Done well")
        parts.extend(f"- {item.strip()}" for item in verdict.praise)

    if truncated:
        parts.append(
            "\n> ⚠️ Diff was truncated for the model; verdict reflects the "
            "first portion of the change only."
        )

    parts.append(f"\n---\n🤖 Reviewed by `{model}` via `.github/workflows/gemini-review.yml`.")
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
    flag_map = {
        "approve": "--approve",
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

    conclusion = "failure" if verdict.verdict == "request_changes" else "success"
    post_check(
        repo=repo,
        head_sha=head_sha,
        conclusion=conclusion,
        title=f"Gemini: {verdict.verdict}",
        summary=verdict.summary[:2000],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
