"""Smoke tests for Council — mocked provider, no API calls."""

from __future__ import annotations

from typing import TypeVar

from code_review_council import (
    AgentReport,
    Council,
    FinalArbiter,
    Finding,
    ProjectContext,
)
from code_review_council.agents import SecurityAgent
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeProvider:
    """Returns canned AgentReport per agent name.

    Implements ``BaseProvider`` structurally (the runtime_checkable
    Protocol). Each ``run_structured`` call inspects the system
    instruction for the agent name and dispatches to the matching
    canned response — keeps the test single-double instead of one
    per agent.
    """

    name = "fake-provider"

    def __init__(self, responses: dict[str, AgentReport]) -> None:
        self._responses = responses
        self.call_log: list[str] = []

    def run_structured(
        self,
        *,
        system_instruction: str,
        user_content: str,
        response_schema: type[T],
    ) -> T:
        # Identify which agent is calling by looking for the agent
        # name in the system instruction. Fragile if two agents have
        # overlapping names — fine for MVP with Security + Correctness.
        for name, report in self._responses.items():
            if f"**{name}** reviewer" in system_instruction:
                self.call_log.append(name)
                return response_schema.model_validate(report.model_dump())
        raise AssertionError(
            f"No canned response for system_instruction snippet: {system_instruction[:200]!r}",
        )


def _ctx() -> ProjectContext:
    return ProjectContext(
        stack=["Test stack"],
        in_scope=["auth", "data correctness"],
        out_of_scope=["i18n", "CSP nonces"],
        notes="test-only context",
    )


# ---------------------------------------------------------------------------
# Council orchestrator
# ---------------------------------------------------------------------------


def test_council_runs_each_agent_once() -> None:
    """Each registered agent gets one provider call (when not skipped)."""
    from code_review_council.agents import CorrectnessAgent

    provider = FakeProvider(
        {
            "Security": AgentReport(summary="security clean", findings=[]),
            "Correctness": AgentReport(summary="correctness clean", findings=[]),
        }
    )
    # Explicit roster — the default council now has 5 specialists,
    # tests pin a smaller subset to keep the canned responses scoped.
    council = Council(
        provider=provider,
        context=_ctx(),
        agents=[SecurityAgent(provider, _ctx()), CorrectnessAgent(provider, _ctx())],
    )
    verdict = council.review(
        diff="diff --git a/x b/x\n+ ok",
        pr_title="t",
        parallel=False,  # deterministic order for the assertion
    )
    assert provider.call_log == ["Security", "Correctness"]
    assert verdict.verdict == "comment"
    assert verdict.findings == []


def test_council_passes_findings_through_arbiter() -> None:
    """Findings from both agents merge into the verdict, sorted by severity."""
    from code_review_council.agents import CorrectnessAgent

    provider = FakeProvider(
        {
            "Security": AgentReport(
                summary="found a thing",
                findings=[
                    Finding(
                        severity="HIGH",
                        confidence="HIGH",
                        file="apps/api.py",
                        line=42,
                        issue="missing auth on admin endpoint",
                        suggestion="add require_admin_user dep",
                    ),
                ],
            ),
            "Correctness": AgentReport(
                summary="off-by-one",
                findings=[
                    Finding(
                        severity="MEDIUM",
                        confidence="HIGH",
                        file="apps/api.py",
                        line=99,
                        issue="pagination off-by-one",
                        suggestion="use < not <=",
                    ),
                ],
            ),
        }
    )
    council = Council(
        provider=provider,
        context=_ctx(),
        agents=[SecurityAgent(provider, _ctx()), CorrectnessAgent(provider, _ctx())],
    )
    verdict = council.review(diff="...", pr_title="t", parallel=False)
    assert verdict.verdict == "request_changes"  # HIGH/HIGH = blocking
    assert len(verdict.findings) == 2
    assert verdict.findings[0].severity == "HIGH"
    assert verdict.findings[1].severity == "MEDIUM"
    assert verdict.per_agent == {"Security": 1, "Correctness": 1}


# ---------------------------------------------------------------------------
# FinalArbiter — anti-hallucination filters
# ---------------------------------------------------------------------------


def test_arbiter_drops_low_confidence_non_nit_findings() -> None:
    """LOW-confidence MEDIUM/HIGH findings are filtered; LOW-confidence NIT stays."""
    arbiter = FinalArbiter(_ctx())
    report = AgentReport(
        summary="mixed",
        findings=[
            Finding(
                severity="HIGH",
                confidence="LOW",  # speculative → dropped
                file="x.py",
                line=1,
                issue="what if user does X",
                suggestion="...",
            ),
            Finding(
                severity="NIT",
                confidence="LOW",  # NIT at LOW confidence stays
                file="x.py",
                line=2,
                issue="naming nit",
                suggestion="...",
            ),
            Finding(
                severity="MEDIUM",
                confidence="HIGH",  # high-confidence stays
                file="x.py",
                line=3,
                issue="real issue",
                suggestion="...",
            ),
        ],
    )
    verdict = arbiter.arbitrate([report])
    assert len(verdict.findings) == 2
    severities = {f.severity for f in verdict.findings}
    assert severities == {"NIT", "MEDIUM"}
    # No blocking finding kept → comment, not request_changes
    assert verdict.verdict == "comment"


def test_arbiter_dedupes_by_file_line_keeping_higher_severity() -> None:
    """When two agents flag the same line, the more severe wins."""
    arbiter = FinalArbiter(_ctx())
    reports = [
        AgentReport(
            summary="security take",
            findings=[
                Finding(
                    severity="HIGH",
                    confidence="HIGH",
                    file="x.py",
                    line=10,
                    issue="auth bypass via X",
                    suggestion="...",
                    agent="Security",
                ),
            ],
        ),
        AgentReport(
            summary="correctness take",
            findings=[
                Finding(
                    severity="MEDIUM",
                    confidence="HIGH",
                    file="x.py",
                    line=10,
                    issue="error handling on X",
                    suggestion="...",
                    agent="Correctness",
                ),
            ],
        ),
    ]
    verdict = arbiter.arbitrate(reports)
    assert len(verdict.findings) == 1
    assert verdict.findings[0].severity == "HIGH"
    assert verdict.findings[0].agent == "Security"


def test_arbiter_drops_out_of_scope_phrase_matches() -> None:
    """Belt-and-braces: out-of-scope keywords cause drops."""
    arbiter = FinalArbiter(_ctx())  # _ctx() lists 'i18n' as out-of-scope
    report = AgentReport(
        summary="...",
        findings=[
            Finding(
                severity="MEDIUM",
                confidence="HIGH",
                file="x.tsx",
                line=5,
                issue="No i18n support for the new label",  # contains 'i18n'
                suggestion="add a translation key",
            ),
            Finding(
                severity="MEDIUM",
                confidence="HIGH",
                file="x.tsx",
                line=6,
                issue="Unhandled null in the response body",
                suggestion="add a guard",
            ),
        ],
    )
    verdict = arbiter.arbitrate([report])
    assert len(verdict.findings) == 1
    assert "i18n" not in verdict.findings[0].issue.lower()


def test_arbiter_signals_review_complete_when_all_agents_done_and_no_findings() -> None:
    arbiter = FinalArbiter(_ctx())
    reports = [
        AgentReport(summary="ok", findings=[], review_complete=True),
        AgentReport(summary="ok", findings=[], review_complete=True),
    ]
    verdict = arbiter.arbitrate(reports)
    assert verdict.review_complete is True
    assert verdict.verdict == "comment"
    assert "no critical issues found" in verdict.summary.lower()


def test_arbiter_does_not_signal_complete_when_findings_remain() -> None:
    """Even if every agent says ``review_complete``, surviving findings veto."""
    arbiter = FinalArbiter(_ctx())
    reports = [
        AgentReport(
            summary="ok",
            findings=[
                Finding(
                    severity="LOW",
                    confidence="HIGH",
                    file="x.py",
                    line=1,
                    issue="quality issue",
                    suggestion="...",
                ),
            ],
            review_complete=True,
        ),
    ]
    verdict = arbiter.arbitrate(reports)
    assert verdict.review_complete is False
    assert len(verdict.findings) == 1


# ---------------------------------------------------------------------------
# Agent prompt sanity
# ---------------------------------------------------------------------------


def test_security_agent_prompt_lists_scope_and_out_of_scope() -> None:
    """Sanity check that the prompt template includes the right hooks."""
    provider = FakeProvider({"Security": AgentReport(summary="", findings=[])})
    agent = SecurityAgent(provider, _ctx())
    prompt = agent.build_system_instruction()
    assert "**Security** reviewer" in prompt
    assert "authentication" in prompt.lower()
    assert "i18n" in prompt  # out-of-scope item visible to the model
    assert "CSP nonces" in prompt
    # Anti-flattery clause is mandatory
    assert "don't open the summary with 'looks good'" in prompt.lower()


# ---------------------------------------------------------------------------
# skipIf hook + V2 specialists
# ---------------------------------------------------------------------------


def test_frontend_agent_skips_pure_backend_diff() -> None:
    from code_review_council.agents import FrontendAgent

    pure_python_diff = (
        "diff --git a/apps/engine/foo.py b/apps/engine/foo.py\n"
        "+++ b/apps/engine/foo.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    assert FrontendAgent.should_run(pure_python_diff) is False


def test_frontend_agent_runs_when_diff_touches_tsx() -> None:
    from code_review_council.agents import FrontendAgent

    mixed_diff = (
        "diff --git a/apps/dashboard/src/page.tsx b/apps/dashboard/src/page.tsx\n"
        "+++ b/apps/dashboard/src/page.tsx\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
    )
    assert FrontendAgent.should_run(mixed_diff) is True


def test_database_agent_runs_on_engine_persistence_diff() -> None:
    from code_review_council.agents import DatabaseAgent

    diff = (
        "diff --git a/apps/engine/src/dap_engine/persistence/runs.py "
        "b/apps/engine/src/dap_engine/persistence/runs.py\n"
        "+++ b/apps/engine/src/dap_engine/persistence/runs.py\n"
        "@@ -1 +1 @@\n"
        "+x\n"
    )
    assert DatabaseAgent.should_run(diff) is True


def test_database_agent_skips_pure_frontend_diff() -> None:
    from code_review_council.agents import DatabaseAgent

    diff = (
        "diff --git a/apps/dashboard/page.tsx b/apps/dashboard/page.tsx\n"
        "+++ b/apps/dashboard/page.tsx\n"
        "@@ -1 +1 @@\n"
        "+x\n"
    )
    assert DatabaseAgent.should_run(diff) is False


def test_council_skips_agents_per_should_run() -> None:
    """A pure-backend diff doesn't invoke FrontendAgent's provider."""
    from code_review_council.agents import FrontendAgent

    provider = FakeProvider(
        {
            "Security": AgentReport(summary="ok", findings=[]),
            "Frontend": AgentReport(summary="should not be called", findings=[]),
        }
    )
    council = Council(
        provider=provider,
        context=_ctx(),
        agents=[SecurityAgent(provider, _ctx()), FrontendAgent(provider, _ctx())],
    )
    verdict = council.review(
        diff=(
            "diff --git a/apps/engine/foo.py b/apps/engine/foo.py\n"
            "+++ b/apps/engine/foo.py\n"
            "@@ -1 +1 @@\n"
            "+x\n"
        ),
        parallel=False,
    )
    assert provider.call_log == ["Security"]
    assert "Frontend" not in provider.call_log
    assert verdict.verdict == "comment"


def test_council_returns_empty_verdict_when_every_agent_skips() -> None:
    from code_review_council.agents import DatabaseAgent, FrontendAgent

    provider = FakeProvider({})
    council = Council(
        provider=provider,
        context=_ctx(),
        agents=[DatabaseAgent(provider, _ctx()), FrontendAgent(provider, _ctx())],
    )
    docs_diff = "diff --git a/README.md b/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n"
    verdict = council.review(diff=docs_diff)
    assert provider.call_log == []
    assert verdict.findings == []
    assert verdict.review_complete is False


def test_council_parallel_path_returns_same_verdict_as_sequential() -> None:
    """Parallel and sequential should produce identical verdicts on fixed input."""
    from code_review_council.agents import CorrectnessAgent

    canned = {
        "Security": AgentReport(
            summary="sec",
            findings=[
                Finding(
                    severity="MEDIUM",
                    confidence="HIGH",
                    file="x.py",
                    line=1,
                    issue="s",
                    suggestion="s",
                ),
            ],
        ),
        "Correctness": AgentReport(
            summary="corr",
            findings=[
                Finding(
                    severity="LOW",
                    confidence="HIGH",
                    file="x.py",
                    line=2,
                    issue="c",
                    suggestion="c",
                ),
            ],
        ),
    }

    def fresh_council() -> Council:
        provider = FakeProvider(canned)
        return Council(
            provider=provider,
            context=_ctx(),
            agents=[SecurityAgent(provider, _ctx()), CorrectnessAgent(provider, _ctx())],
        )

    diff = "diff --git a/x.py b/x.py\n+++ b/x.py\n@@\n+x\n"
    seq = fresh_council().review(diff=diff, parallel=False)
    par = fresh_council().review(diff=diff, parallel=True)
    assert seq.verdict == par.verdict
    assert sorted(f.file + str(f.line) for f in seq.findings) == sorted(
        f.file + str(f.line) for f in par.findings
    )


# ---------------------------------------------------------------------------
# End-to-end load-bearing check: HIGH/LOW must NEVER block, even when an
# agent returns it. This is the single guarantee the whole Confidence
# field exists to enforce — if this regresses, every "HIGH" tag becomes
# a false alarm vector.
# ---------------------------------------------------------------------------


def test_high_severity_low_confidence_never_blocks_end_to_end() -> None:
    """A HIGH-severity / LOW-confidence finding from an agent must NOT block.

    Construction: a single mock agent that ALWAYS returns one
    HIGH/LOW finding, sent through a real Council. If the arbiter's
    LOW-confidence drop is ever bypassed (refactor regression, prompt
    change, anything), this test catches it: the verdict would flip
    from ``comment`` to ``request_changes`` and the assertion fails.

    This is the test the user explicitly asked for — "sprytny agent-
    mock który zawsze zwraca HIGH severity / LOW confidence". It
    exercises the full Council pipeline, not just the arbiter in
    isolation.
    """
    from code_review_council.agents import CorrectnessAgent

    provider = FakeProvider(
        {
            # The "Correctness" agent returns one HIGH-severity finding
            # at LOW confidence — i.e. a hypothetical concern dressed
            # up as a blocker. Arbiter MUST drop it.
            "Correctness": AgentReport(
                summary="suspicious pattern",
                findings=[
                    Finding(
                        severity="HIGH",
                        confidence="LOW",  # the load-bearing combo
                        file="apps/api.py",
                        line=1,
                        issue="what if user does X — could be a bug",
                        suggestion="add a guard, maybe?",
                    ),
                ],
            ),
        }
    )
    council = Council(
        provider=provider,
        context=_ctx(),
        agents=[CorrectnessAgent(provider, _ctx())],
    )
    verdict = council.review(diff="x", parallel=False)

    # The HIGH/LOW finding must be filtered out.
    assert verdict.findings == [], (
        f"LOW-confidence HIGH finding leaked through arbiter: {verdict.findings}"
    )
    # Verdict must NOT escalate to request_changes — that would defeat
    # the whole Confidence-gate design.
    assert verdict.verdict == "comment", (
        f"LOW-confidence HIGH triggered block verdict: {verdict.verdict}"
    )


# ---------------------------------------------------------------------------
# Partial-council resilience: one agent throwing must NOT discard the others
# ---------------------------------------------------------------------------


def test_council_isolates_failing_agent_in_parallel_mode() -> None:
    """When one agent's provider call raises, the others' results still land.

    Realistic case: OpenRouter rate-limits one specialist mid-run.
    Pre-fix, ``asyncio.gather`` would propagate the exception and
    discard the 4 other agents' work. Post-fix
    (``return_exceptions=True`` + ``_failure_report``), the failed
    agent surfaces as a single LOW finding ("this lane was not
    reviewed") and the others' real findings come through.
    """
    from code_review_council.agents import CorrectnessAgent

    class FlakyProvider:
        """Security succeeds; Correctness raises a synthetic timeout."""

        name = "flaky"

        def run_structured(
            self,
            *,
            system_instruction: str,
            user_content: str,
            response_schema: type[BaseModel],
        ) -> BaseModel:
            if "**Security** reviewer" in system_instruction:
                return response_schema.model_validate(
                    AgentReport(summary="security clean", findings=[]).model_dump(),
                )
            raise TimeoutError("Provider rate-limited Correctness agent")

    provider = FlakyProvider()
    council = Council(
        provider=provider,  # type: ignore[arg-type]
        context=_ctx(),
        agents=[
            SecurityAgent(provider, _ctx()),  # type: ignore[arg-type]
            CorrectnessAgent(provider, _ctx()),  # type: ignore[arg-type]
        ],
    )
    verdict = council.review(diff="x", parallel=True)

    # Security's success path went through — verdict is non-blocking.
    assert verdict.verdict == "comment"
    breadcrumbs = [f for f in verdict.findings if f.file == "(council infrastructure)"]
    assert len(breadcrumbs) == 1, (
        f"Expected exactly 1 failure breadcrumb, got {len(breadcrumbs)}: {breadcrumbs}"
    )
    breadcrumb = breadcrumbs[0]
    assert breadcrumb.agent == "Correctness"
    assert breadcrumb.severity == "LOW"  # never blocks
    assert "TimeoutError" in breadcrumb.issue
    # The provider's error message MUST NOT appear — it could contain
    # API keys / response bodies depending on the provider.
    assert "rate-limited" not in breadcrumb.issue, (
        f"Failure breadcrumb leaked provider error message: {breadcrumb.issue}"
    )


def test_council_isolates_failing_agent_in_sequential_mode() -> None:
    """Sequential mode applies the same isolation as parallel.

    Keeps the two execution paths behaviour-equivalent.
    """
    from code_review_council.agents import CorrectnessAgent

    class FlakyProvider:
        name = "flaky"

        def run_structured(
            self,
            *,
            system_instruction: str,
            user_content: str,
            response_schema: type[BaseModel],
        ) -> BaseModel:
            if "**Security** reviewer" in system_instruction:
                return response_schema.model_validate(
                    AgentReport(summary="ok", findings=[]).model_dump(),
                )
            raise RuntimeError("Synthetic provider failure")

    provider = FlakyProvider()
    council = Council(
        provider=provider,  # type: ignore[arg-type]
        context=_ctx(),
        agents=[
            SecurityAgent(provider, _ctx()),  # type: ignore[arg-type]
            CorrectnessAgent(provider, _ctx()),  # type: ignore[arg-type]
        ],
    )
    verdict = council.review(diff="x", parallel=False)

    breadcrumbs = [f for f in verdict.findings if f.file == "(council infrastructure)"]
    assert len(breadcrumbs) == 1
    assert breadcrumbs[0].agent == "Correctness"
    assert "Synthetic" not in breadcrumbs[0].issue  # no provider-message leak
