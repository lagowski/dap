"""Command-line entry point: ``code-review-council`` / ``python -m code_review_council``.

Reads a unified diff (a file, stdin, or ``git diff <base>...<head>``), runs the
Council with the chosen provider, and prints the arbitrated verdict as JSON
and/or Markdown.

Exit codes:

- ``0``: the review ran (findings or not), or ``--fail-on none``.
- ``1``: ``--fail-on high`` and a HIGH or CRITICAL finding survived the arbiter.
- ``2``: usage or configuration error (missing API key, unreadable diff or
  context file, bad arguments).

The API key is read from an environment variable only (never a flag), so it
does not land in shell history or a process listing, and it is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from code_review_council.council import Council
from code_review_council.models import Finding, ProjectContext, ReviewVerdict
from code_review_council.providers.base import BaseProvider
from code_review_council.providers.gemini import GeminiProvider
from code_review_council.providers.openrouter import OpenRouterProvider
from code_review_council.sanitize import strip_marker_tags

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

PROVIDERS = ("gemini", "openrouter")
DEFAULT_KEY_ENV = {"gemini": "GEMINI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
BLOCKING_SEVERITIES = frozenset({"CRITICAL", "HIGH"})

ProviderFactory = Callable[[str, str, str | None], BaseProvider]

_SEVERITY_EMOJI = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🟡",
    "LOW": "🔵",
    "NIT": "⚪",
}
_CONFIDENCE_BADGE = {"HIGH": "verified", "MEDIUM": "likely", "LOW": "hypothetical"}


class UsageError(Exception):
    """A configuration problem the user can fix; exits with code 2."""


def build_provider(provider: str, api_key: str, model: str | None) -> BaseProvider:
    """Construct the real provider for ``provider`` (``gemini`` or ``openrouter``)."""
    built: GeminiProvider | OpenRouterProvider
    if provider == "gemini":
        built = (
            GeminiProvider(api_key=api_key, model=model)
            if model
            else GeminiProvider(api_key=api_key)
        )
    else:
        built = (
            OpenRouterProvider(api_key=api_key, model=model)
            if model
            else OpenRouterProvider(api_key=api_key)
        )
    # The providers expose ``name`` as a read-only property; the Protocol
    # declares a plain attribute, which mypy reads as settable.
    return cast("BaseProvider", built)


def load_context(path: Path) -> ProjectContext:
    """Load a :class:`ProjectContext` from a TOML, JSON or plain-text file.

    ``.toml`` and ``.json`` files carry the fields of ``ProjectContext``
    (``stack``, ``in_scope``, ``out_of_scope``, ``notes``). Any other file is
    taken as free-form ``notes``.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UsageError(f"cannot read context file {path}: {exc.strerror}") from exc
    suffix = path.suffix.lower()
    try:
        if suffix == ".toml":
            data: object = tomllib.loads(text)
        elif suffix == ".json":
            data = json.loads(text)
        else:
            return ProjectContext(stack=[], notes=text.strip())
        if not isinstance(data, dict):
            raise UsageError(f"context file {path} must hold a table/object")
        data.setdefault("stack", [])
        return ProjectContext.model_validate(data)
    except (tomllib.TOMLDecodeError, json.JSONDecodeError, ValidationError) as exc:
        first = str(exc).splitlines()[0]
        raise UsageError(f"invalid context file {path}: {first}") from exc


def read_diff(args: argparse.Namespace) -> str:
    """Return the diff from ``--diff`` (path or ``-``), ``--base/--head``, or stdin."""
    if args.diff and (args.base or args.head):
        raise UsageError("use either --diff or --base/--head, not both")
    if bool(args.base) != bool(args.head):
        raise UsageError("--base and --head must be given together")
    if args.base:
        proc = subprocess.run(
            ["git", "diff", "--no-color", f"{args.base}...{args.head}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise UsageError(f"git diff {args.base}...{args.head} failed: {proc.stderr.strip()}")
        return proc.stdout
    if args.diff and args.diff != "-":
        try:
            return Path(args.diff).read_text(encoding="utf-8")
        except OSError as exc:
            raise UsageError(f"cannot read diff {args.diff}: {exc.strerror}") from exc
    if not args.diff and sys.stdin.isatty():
        raise UsageError("no diff given: pass --diff FILE, --diff - (stdin) or --base/--head")
    return sys.stdin.read()


def _format_finding(finding: Finding) -> str:
    sev = (finding.severity or "").upper()
    conf = (finding.confidence or "").upper()
    badge = _CONFIDENCE_BADGE.get(conf, conf.lower() or "?")
    location = strip_marker_tags(finding.file) + (f":{finding.line}" if finding.line else "")
    agent = f" · _{finding.agent}_" if finding.agent else ""
    body = (
        f"#### {_SEVERITY_EMOJI.get(sev, '•')} {sev} · _{badge}_{agent} — `{location}`\n"
        f"**Issue:** {strip_marker_tags(finding.issue).strip()}\n\n"
        f"**Suggestion:** {strip_marker_tags(finding.suggestion).strip()}"
    )
    evidence = strip_marker_tags(finding.evidence).strip()
    if evidence and evidence != "—":
        body += f"\n\n**Evidence:**\n```\n{evidence.replace('```', '` ` `')}\n```"
    return body


def render_markdown(verdict: ReviewVerdict, *, model_label: str) -> str:
    """Render the verdict as one PR-comment Markdown body."""
    counts: dict[str, int] = {}
    for f in verdict.findings:
        sev = (f.severity or "").upper()
        counts[sev] = counts.get(sev, 0) + 1
    scoreboard = " · ".join(
        f"{_SEVERITY_EMOJI[sev]} {sev}: {counts[sev]}"
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "NIT")
        if counts.get(sev)
    )
    parts = ["## Code review council", f"**Findings:** {scoreboard or 'no actionable findings'}"]
    if verdict.per_agent:
        parts.append(
            "**By agent:** " + " · ".join(f"_{a}_: {n}" for a, n in verdict.per_agent.items())
        )
    parts += ["", strip_marker_tags(verdict.summary).strip()]
    if verdict.findings:
        parts.append("\n### Findings")
        for f in verdict.findings:
            parts += ["", _format_finding(f)]
    parts.append(f"\n---\n🤖 Reviewed by the DAP Code Review Council (`{model_label}`).")
    return "\n".join(parts) + "\n"


def has_blocking(verdict: ReviewVerdict) -> bool:
    """True when a HIGH or CRITICAL finding survived the arbiter."""
    return any((f.severity or "").upper() in BLOCKING_SEVERITIES for f in verdict.findings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-review-council",
        description=(
            "Multi-agent code review of a diff. Scoped specialists review the change and "
            "a final arbiter drops duplicates and low-confidence findings."
        ),
    )
    src = parser.add_argument_group("diff source (pick one; default: stdin)")
    src.add_argument("--diff", metavar="FILE", help="unified diff file, or - for stdin")
    src.add_argument("--base", metavar="REF", help="git base ref (diff is base...head)")
    src.add_argument("--head", metavar="REF", help="git head ref")
    parser.add_argument("--provider", choices=PROVIDERS, default="gemini")
    parser.add_argument("--model", help="model name (default: the provider's default)")
    parser.add_argument(
        "--api-key-env",
        metavar="VAR",
        help="environment variable holding the API key "
        "(default: GEMINI_API_KEY or OPENROUTER_API_KEY)",
    )
    parser.add_argument(
        "--context-file",
        type=Path,
        metavar="FILE",
        help="project context: .toml/.json with stack, in_scope, out_of_scope, notes; "
        "any other file is used as free-form notes",
    )
    parser.add_argument("--title", default="", help="PR title, passed to the reviewers")
    parser.add_argument("--body", default="", help="PR description, passed to the reviewers")
    parser.add_argument(
        "--format",
        choices=("both", "json", "markdown", "none"),
        default="both",
        help="what to print on stdout (default: JSON, then Markdown)",
    )
    parser.add_argument("--json-out", type=Path, metavar="FILE", help="also write JSON here")
    parser.add_argument(
        "--markdown-out", type=Path, metavar="FILE", help="also write Markdown here"
    )
    parser.add_argument(
        "--fail-on",
        choices=("none", "high"),
        default="none",
        help="exit 1 when a HIGH or CRITICAL finding survives the arbiter",
    )
    return parser


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(
    argv: Sequence[str] | None = None,
    *,
    provider_factory: ProviderFactory = build_provider,
) -> int:
    """Run the CLI and return its exit code. ``provider_factory`` is the test seam."""
    args = build_parser().parse_args(argv)
    key_env = args.api_key_env or DEFAULT_KEY_ENV[args.provider]
    try:
        api_key = os.environ.get(key_env, "").strip()
        if not api_key:
            raise UsageError(
                f"API key not set: export {key_env} with your {args.provider} API key "
                "(or name another variable with --api-key-env)"
            )
        context = load_context(args.context_file) if args.context_file else ProjectContext(stack=[])
        diff = read_diff(args)
        provider = provider_factory(args.provider, api_key, args.model)
    except UsageError as exc:
        print(f"code-review-council: error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    verdict = Council(provider=provider, context=context).review(
        diff=diff, pr_title=args.title, pr_body=args.body
    )
    as_json = json.dumps(verdict.model_dump(), indent=2, ensure_ascii=False)
    as_md = render_markdown(verdict, model_label=f"{args.provider}:{provider.name}")
    if args.json_out:
        _write(args.json_out, as_json + "\n")
    if args.markdown_out:
        _write(args.markdown_out, as_md)
    if args.format in ("both", "json"):
        print(as_json)
    if args.format == "both":
        print()
    if args.format in ("both", "markdown"):
        print(as_md, end="")

    if args.fail_on == "high" and has_blocking(verdict):
        print(
            "code-review-council: a HIGH or CRITICAL finding survived the arbiter",
            file=sys.stderr,
        )
        return EXIT_FINDINGS
    return EXIT_OK


def run() -> None:
    """Console-script entry point."""
    sys.exit(main())
