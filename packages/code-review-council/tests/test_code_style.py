"""Tests for the opt-in CodeStyleAgent specialist (spike, issue #633).

Two things matter and are tested here:

1. **The specialist's prompt is well-formed** — mirrors the
   ``test_security_agent_prompt_lists_scope_and_out_of_scope`` shape:
   the system instruction carries the agent name, its scope hooks,
   the project's out-of-scope items, and the mandatory anti-flattery
   clause from the base template.
2. **The opt-in switch gates the default roster.** With the
   ``COUNCIL_ENABLE_CODE_STYLE`` env var unset (default), the roster
   is byte-for-byte the pre-spike 5-agent roster — no CodeStyle. With
   the env var truthy, CodeStyle is appended as the 6th specialist.

No live model calls: the agent is constructed with a structural
``FakeProvider`` (same approach the smoke tests use) and only its
prompt assembly is exercised, never ``review()``.
"""

from __future__ import annotations

import pytest
from code_review_council import AgentReport, ProjectContext, default_agents
from code_review_council.agents import CodeStyleAgent
from code_review_council.providers.base import BaseProvider


class _FakeProvider:
    """Structural ``BaseProvider`` — never invoked in these tests."""

    name = "fake-provider"

    def run_structured(self, **_: object) -> AgentReport:  # pragma: no cover - unused
        raise AssertionError("CodeStyle tests must not make provider calls")


def _ctx() -> ProjectContext:
    return ProjectContext(
        stack=["Test stack"],
        in_scope=["auth", "data correctness"],
        out_of_scope=["i18n", "CSP nonces"],
        notes="test-only context",
    )


def _provider() -> BaseProvider:
    return _FakeProvider()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Prompt sanity — mirrors test_security_agent_prompt_lists_scope_and_out_of_scope
# ---------------------------------------------------------------------------


def test_code_style_agent_prompt_is_well_formed() -> None:
    agent = CodeStyleAgent(_provider(), _ctx())
    prompt = agent.build_system_instruction()
    # Agent identity + scope hooks present.
    assert "**Code Style** reviewer" in prompt
    assert "naming" in prompt.lower()
    assert "readability" in prompt.lower()
    # Project out-of-scope items still threaded through the base template.
    assert "i18n" in prompt
    assert "CSP nonces" in prompt
    # Mandatory anti-flattery clause inherited from BaseAgent template.
    assert "don't open the summary with 'looks good'" in prompt.lower()


def test_code_style_agent_focus_is_conservative_and_line_cited() -> None:
    """Focus areas emphasise line-by-line citation and conservative severity.

    The spike's whole value-add is precise file:line citations at LOW/NIT
    severity so it can't drown the real bugs. Pin those properties so a
    future prompt edit that loses them is caught.
    """
    agent = CodeStyleAgent(_provider(), _ctx())
    focus_blob = " ".join(agent.focus_areas).lower()
    assert "file:line" in focus_blob
    assert "nit" in focus_blob


# ---------------------------------------------------------------------------
# Opt-in switch — default OFF, env var turns it ON
# ---------------------------------------------------------------------------


def test_code_style_excluded_from_default_roster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default behaviour is unchanged: roster has the original 5 specialists."""
    monkeypatch.delenv("COUNCIL_ENABLE_CODE_STYLE", raising=False)
    agents = default_agents(_provider(), _ctx())
    names = [a.name for a in agents]
    assert names == ["Security", "Correctness", "Database", "Performance", "Frontend"]
    assert "Code Style" not in names


@pytest.mark.parametrize("flag", ["1", "true", "TRUE", "yes", "on"])
def test_code_style_included_when_switch_enabled(
    monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    """A truthy ``COUNCIL_ENABLE_CODE_STYLE`` appends CodeStyle as 6th agent."""
    monkeypatch.setenv("COUNCIL_ENABLE_CODE_STYLE", flag)
    agents = default_agents(_provider(), _ctx())
    names = [a.name for a in agents]
    assert names == [
        "Security",
        "Correctness",
        "Database",
        "Performance",
        "Frontend",
        "Code Style",
    ]


@pytest.mark.parametrize("flag", ["0", "false", "no", "off", ""])
def test_code_style_stays_off_for_falsy_switch(monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    """Falsy / empty values keep the default roster — no accidental enable."""
    monkeypatch.setenv("COUNCIL_ENABLE_CODE_STYLE", flag)
    agents = default_agents(_provider(), _ctx())
    assert "Code Style" not in [a.name for a in agents]
