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
