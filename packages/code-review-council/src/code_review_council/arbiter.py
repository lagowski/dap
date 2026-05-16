"""FinalArbiter — consolidates agent reports into a single verdict.

Three filtering passes happen here, in order:

1. **Low-confidence drop**: any Finding with ``confidence == LOW``
   is removed unless severity is already NIT. This is the headline
   anti-hallucination filter — speculative findings never reach the PR.
2. **Deduplication**: when two agents flag the same file:line, the
   higher-severity finding wins. Same severity → first-seen wins.
3. **Scope re-check (lightweight)**: a small denylist of phrases
   ("i18n", "CSP nonce", etc.) drops findings that crept past the
   per-agent prompt's out-of-scope clause. Defensive only; the
   primary gate is the agent's own scope.

The arbiter does NOT call an LLM today. A future iteration could add
an LLM pass that ranks/dedupes more intelligently, but for two agents
the rule-based filter is enough and avoids a third API call.
"""

from __future__ import annotations

from code_review_council.models import AgentReport, Finding, ProjectContext, ReviewVerdict

# Severity ordinals — lower number = more severe. Used for sort order
# AND for dedup (the more severe finding at a duplicate location wins).
_SEVERITY_ORDER = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
    "NIT": 4,
}


def _is_low_confidence_drop(finding: Finding) -> bool:
    """LOW-confidence findings are dropped unless severity is NIT.

    A NIT-severity finding is fine to keep even if speculative — it's
    a "consider this" hint that doesn't block anything. A MEDIUM that
    the agent itself marked LOW-confidence is exactly the kind of
    speculation that drove the round 4-7 noise on #441.
    """
    confidence = (finding.confidence or "").upper()
    severity = (finding.severity or "").upper()
    return confidence == "LOW" and severity != "NIT"


def _dedupe_by_location(findings: list[Finding]) -> list[Finding]:
    """When two agents flag the same file:line, keep the more severe one.

    A Security finding and a Correctness finding on the same line are
    usually different framings of the same bug; surfacing both noises
    up the PR review. Equal severity → first-seen wins (stable).
    """
    seen: dict[tuple[str, int | None], Finding] = {}
    for finding in findings:
        key = (finding.file, finding.line)
        existing = seen.get(key)
        if existing is None:
            seen[key] = finding
            continue
        new_sev = _SEVERITY_ORDER.get((finding.severity or "").upper(), 99)
        old_sev = _SEVERITY_ORDER.get((existing.severity or "").upper(), 99)
        if new_sev < old_sev:
            seen[key] = finding
    return list(seen.values())


def _scope_recheck(finding: Finding, *, out_of_scope: list[str]) -> bool:
    """Belt-and-braces drop: substring match against out-of-scope phrases.

    Agents are prompted to skip out-of-scope concerns, but a stray
    finding occasionally slips through. The check is intentionally
    crude — exact-match by lowercased substring — because a
    smarter filter risks dropping legitimate findings that happen
    to mention an out-of-scope word.
    """
    if not out_of_scope:
        return True
    haystack = " ".join(
        [
            finding.issue or "",
            finding.suggestion or "",
        ],
    ).lower()
    for needle in out_of_scope:
        needle_lc = needle.strip().lower()
        if needle_lc and needle_lc in haystack:
            return False
    return True


def _sort_findings(findings: list[Finding]) -> list[Finding]:
    """CRITICAL → NIT, stable."""
    return sorted(
        findings,
        key=lambda f: _SEVERITY_ORDER.get((f.severity or "").upper(), 99),
    )


def _build_summary(
    reports: list[AgentReport],
    kept: list[Finding],
) -> str:
    """Compose the final summary from per-agent summaries + kept counts.

    Keeps the human-readable preamble short — the findings themselves
    are the content; the summary is just orientation.
    """
    if not kept:
        # Use the canonical convergence phrase so the renderer can spot it.
        return "No critical issues found after analysis by the review council."

    severities: dict[str, int] = {}
    for f in kept:
        sev = (f.severity or "").upper()
        severities[sev] = severities.get(sev, 0) + 1
    counts_line = ", ".join(f"{n} {sev.lower()}" for sev, n in severities.items() if n > 0)

    per_agent_take = " ".join(s.summary.strip() for s in reports if s.summary and s.summary.strip())
    return f"Council flagged {counts_line}. {per_agent_take}".strip()


class FinalArbiter:
    """Combine N AgentReports into one ReviewVerdict."""

    def __init__(self, context: ProjectContext) -> None:
        self._context = context

    def arbitrate(self, reports: list[AgentReport]) -> ReviewVerdict:
        all_findings: list[Finding] = []
        for report in reports:
            all_findings.extend(report.findings)

        # Pass 1: drop LOW-confidence non-NIT speculation.
        post_confidence = [f for f in all_findings if not _is_low_confidence_drop(f)]
        # Pass 2: dedupe by (file, line).
        post_dedup = _dedupe_by_location(post_confidence)
        # Pass 3: out-of-scope phrase guard (belt-and-braces).
        post_scope = [
            f for f in post_dedup if _scope_recheck(f, out_of_scope=self._context.out_of_scope)
        ]
        kept = _sort_findings(post_scope)

        # Convergence: every contributing agent reports done, AND we
        # didn't keep anything blocking.
        every_agent_done = bool(reports) and all(r.review_complete for r in reports)
        has_blocking = any(
            (f.severity or "").upper() == "CRITICAL"
            or ((f.severity or "").upper() == "HIGH" and (f.confidence or "").upper() != "LOW")
            for f in kept
        )

        per_agent: dict[str, int] = {}
        for f in kept:
            agent = f.agent or "unknown"
            per_agent[agent] = per_agent.get(agent, 0) + 1

        return ReviewVerdict(
            verdict="request_changes" if has_blocking else "comment",
            summary=_build_summary(reports, kept),
            findings=kept,
            review_complete=every_agent_done and not kept,
            per_agent=per_agent,
        )
