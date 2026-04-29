"""Tests for dap_prompt_dsl.build_prompt — pure function semantics."""

from __future__ import annotations

import pytest
from dap_prompt_dsl import PromptBuildError, build_prompt


def test_build_simple_template() -> None:
    template = "<agent_prompt><role>{{ role }}</role></agent_prompt>"
    result = build_prompt(template, {"role": "test_author"})
    assert result.valid is True
    assert "<role>test_author</role>" in result.xml
    assert result.errors == []


def test_build_with_loops_and_conditionals() -> None:
    template = (
        "<agent_prompt>"
        "<role>{{ role }}</role>"
        "<constraints>"
        "{% for c in constraints %}<{{ c }}/>{% endfor %}"
        "</constraints>"
        "</agent_prompt>"
    )
    result = build_prompt(
        template,
        {"role": "test_author", "constraints": ["no_implementation", "tests_must_fail"]},
    )
    assert result.valid is True
    assert "<no_implementation/>" in result.xml
    assert "<tests_must_fail/>" in result.xml


def test_undefined_variable_raises() -> None:
    template = "<agent_prompt><role>{{ missing_var }}</role></agent_prompt>"
    with pytest.raises(PromptBuildError) as exc_info:
        build_prompt(template, {})
    assert "undefined" in str(exc_info.value).lower()


def test_template_syntax_error() -> None:
    template = "<agent_prompt>{% invalid %}</agent_prompt>"
    with pytest.raises(PromptBuildError) as exc_info:
        build_prompt(template, {})
    assert "syntax" in str(exc_info.value).lower()


def test_invalid_xml_output_marked_invalid() -> None:
    """Template renders text that is not well-formed XML."""
    template = "<agent_prompt><unclosed></agent_prompt>"
    result = build_prompt(template, {})
    assert result.valid is False
    assert any("parse" in e.lower() for e in result.errors)


def test_wrong_root_element_marked_invalid() -> None:
    template = "<wrong_root><role>foo</role></wrong_root>"
    result = build_prompt(template, {})
    assert result.valid is False
    assert any("root element" in e.lower() for e in result.errors)


def test_custom_expected_root() -> None:
    template = "<custom_prompt><role>x</role></custom_prompt>"
    result = build_prompt(template, {}, expected_root="custom_prompt")
    assert result.valid is True


def test_sandbox_blocks_attribute_access() -> None:
    """Jinja sandbox should reject access to dunder methods."""
    template = "<agent_prompt>{{ ''.__class__.__bases__ }}</agent_prompt>"
    with pytest.raises(PromptBuildError):
        build_prompt(template, {})


def test_xxe_attempt_blocked() -> None:
    """defusedxml should reject DOCTYPE declarations."""
    malicious = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        "<agent_prompt><role>&xxe;</role></agent_prompt>"
    )
    template = "{{ payload }}"
    result = build_prompt(template, {"payload": malicious})
    assert result.valid is False
    # Root cause: either DOCTYPE forbidden or external entity rejected
    assert result.errors


def test_pure_function_idempotent() -> None:
    """Same input → same output, no state."""
    template = "<agent_prompt><role>{{ role }}</role></agent_prompt>"
    ctx = {"role": "verifier"}
    r1 = build_prompt(template, ctx)
    r2 = build_prompt(template, ctx)
    assert r1.xml == r2.xml
    assert r1.valid == r2.valid


# ---------------------------------------------------------------------------
# input_schema scoping (#60, v0.5)
# ---------------------------------------------------------------------------


def test_input_schema_allows_declared_field() -> None:
    """A field listed in input_schema renders normally."""
    template = "<agent_prompt><role>{{ role }}</role></agent_prompt>"
    result = build_prompt(
        template,
        {"role": "verifier", "max_attempts": 3},
        input_schema=["role"],
    )
    assert result.valid is True
    assert "<role>verifier</role>" in result.xml


def test_input_schema_blocks_undeclared_field() -> None:
    """A field NOT in input_schema is invisible to the template even if context has it."""
    template = "<agent_prompt><role>{{ role }}</role></agent_prompt>"
    with pytest.raises(PromptBuildError) as exc_info:
        build_prompt(
            template,
            {"role": "verifier"},
            input_schema=["max_attempts"],  # role intentionally not declared
        )
    msg = str(exc_info.value)
    assert "role" in msg
    assert "input_schema" in msg
    assert "max_attempts" in msg  # surface the currently-declared inputs


def test_input_schema_empty_passes_through() -> None:
    """Empty list = backward compat — full state remains visible."""
    template = "<agent_prompt><role>{{ role }}</role></agent_prompt>"
    result = build_prompt(
        template,
        {"role": "verifier", "anything_else": True},
        input_schema=[],
    )
    assert result.valid is True


def test_input_schema_none_passes_through() -> None:
    """None = backward compat — full state remains visible."""
    template = "<agent_prompt><role>{{ role }}</role></agent_prompt>"
    result = build_prompt(
        template,
        {"role": "verifier", "anything_else": True},
        input_schema=None,
    )
    assert result.valid is True


def test_input_schema_drops_extra_context_silently() -> None:
    """Context fields outside the schema are dropped — they never reach the template."""
    template = (
        "<agent_prompt><role>{{ role }}</role><count>{{ max_attempts }}</count></agent_prompt>"
    )
    # Schema declares role + max_attempts; context also has 'sneaky' which
    # the template doesn't reference. Build should succeed; sneaky's value
    # never lands in the rendered XML.
    result = build_prompt(
        template,
        {"role": "v", "max_attempts": 5, "sneaky": "do not leak"},
        input_schema=["role", "max_attempts"],
    )
    assert result.valid is True
    assert "do not leak" not in result.xml
    assert "<count>5</count>" in result.xml


def test_input_schema_field_declared_but_missing_in_context_distinct_hint() -> None:
    """Field declared in schema but not in context → tailored hint, not the schema-fix one.

    Two failure modes deserve different hints:

    1. Field NOT in input_schema → the fix is to extend the schema
       (or remove the reference).
    2. Field IS in input_schema but caller forgot to supply it → the
       fix is to pass it in the context, NOT to touch the schema.

    The error must point the user at the right fix or it sends them
    on a wild goose chase.
    """
    template = "<agent_prompt><role>{{ role }}</role></agent_prompt>"
    with pytest.raises(PromptBuildError) as exc_info:
        build_prompt(
            template,
            {},  # role not supplied
            input_schema=["role"],
        )
    msg = str(exc_info.value)
    assert "role" in msg
    assert "declared" in msg
    assert "render context" in msg
    # And the misleading "Add the field to agent.input_schema" hint must NOT appear.
    assert "Add the field to agent.input_schema" not in msg


def test_complex_test_authoring_template() -> None:
    """End-to-end shape from DOCUMENTATION.md section 11.4."""
    template = """<agent_prompt version="1">
<role>{{ role }}</role>
<task>{{ task }}</task>
<user_story id="{{ user_story.id }}">
<description>{{ user_story.description }}</description>
<acceptance_criteria>
{% for c in user_story.acceptance_criteria %}<criterion>{{ c }}</criterion>
{% endfor %}</acceptance_criteria>
</user_story>
<code_context>
<language>{{ code_context.language }}</language>
<framework>{{ code_context.framework }}</framework>
</code_context>
<output>
<format>{{ output.format }}</format>
<location>{{ output.location }}</location>
</output>
</agent_prompt>"""
    result = build_prompt(
        template,
        {
            "role": "test_author",
            "task": "Generate failing tests only.",
            "user_story": {
                "id": "US-123",
                "description": "Upload CSV",
                "acceptance_criteria": ["Invalid rows reported", "Valid rows processed"],
            },
            "code_context": {"language": "python", "framework": "pytest"},
            "output": {"format": "code", "location": "tests/"},
        },
    )
    assert result.valid is True
    assert 'id="US-123"' in result.xml
    assert "<criterion>Invalid rows reported</criterion>" in result.xml
    assert "<framework>pytest</framework>" in result.xml


# ---------------------------------------------------------------------------
# Agent metadata injection (#113)
# ---------------------------------------------------------------------------


def test_agent_metadata_visible_to_template_when_input_schema_set() -> None:
    """``agent_metadata`` bypasses ``input_schema`` projection — agents
    can reference ``{{ role }}`` without declaring it as an input."""
    template = "<agent_prompt><role>{{ role }}</role></agent_prompt>"
    result = build_prompt(
        template,
        {"selected_issue_ids": [1]},
        input_schema=["selected_issue_ids"],
        agent_metadata={"role": "implementer"},
    )
    assert result.valid is True
    assert "<role>implementer</role>" in result.xml


def test_agent_metadata_does_not_mutate_caller_context() -> None:
    """Caller's ``context`` dict must not gain reserved metadata keys
    after the render — would otherwise leak between node calls when
    callers reuse the same dict."""
    template = "<agent_prompt><role>{{ role }}</role></agent_prompt>"
    context: dict[str, object] = {}
    build_prompt(template, context, agent_metadata={"role": "verifier"})
    assert "role" not in context


def test_caller_context_wins_over_agent_metadata_on_collision() -> None:
    """``role`` from ``context`` overrides the metadata value — gives
    callers an escape hatch to test what a different role sees."""
    template = "<agent_prompt><role>{{ role }}</role></agent_prompt>"
    result = build_prompt(
        template,
        {"role": "from-context"},
        agent_metadata={"role": "from-metadata"},
    )
    assert result.valid is True
    assert "<role>from-context</role>" in result.xml
