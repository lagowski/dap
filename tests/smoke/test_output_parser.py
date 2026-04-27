"""Tests for the per-agent output parser.

The parser turns raw adapter text (LLM completion or shell stdout) into a
state diff. Resolution: ``output_schema`` (the agent's declared subset)
wins; falls back to ``ROLE_FIELDS[role]``; custom roles with neither
are skipped (the engine falls back to permissive structured-field
merging).
"""

from __future__ import annotations

from dap_engine.execution.output_parser import parse_node_output


def test_unknown_role_is_skipped() -> None:
    """No schema → ParseResult.skipped — caller falls back to structured merge."""
    result = parse_node_output("custom-role", [], '{"foo": "bar"}')
    assert result.success is True
    assert result.skipped is True
    assert result.parsed == {}


def test_task_selector_extracts_from_output_tag() -> None:
    text = (
        "Picked the two issues most aligned with the user goal:\n"
        '<output>{"selected_issue_ids": [123, 456]}</output>\n'
        "Done."
    )
    result = parse_node_output("task_selector", [], text)
    assert result.success is True
    assert result.parsed == {"selected_issue_ids": [123, 456]}


def test_task_selector_extracts_from_markdown_fence() -> None:
    text = 'Reasoning…\n```json\n{\n  "selected_issue_ids": [42]\n}\n```\n'
    result = parse_node_output("task_selector", [], text)
    assert result.success is True
    assert result.parsed == {"selected_issue_ids": [42]}


def test_task_selector_extracts_from_bare_json() -> None:
    """Whole-output-is-JSON works as a last-resort fallback."""
    result = parse_node_output(
        "task_selector", [], '{"selected_issue_ids": [1]}'
    )
    assert result.success is True
    assert result.parsed == {"selected_issue_ids": [1]}


def test_test_author_writes_subset_of_allowed_fields() -> None:
    text = '<output>{"tests_generated": true, "test_files": ["tests/test_widget.py"]}</output>'
    result = parse_node_output("test_author", [], text)
    assert result.success is True
    assert result.parsed == {
        "tests_generated": True,
        "test_files": ["tests/test_widget.py"],
    }
    # Fields not written by the agent are absent (not None-stamped) so
    # they don't clobber state from earlier nodes.
    assert "test_generation_errors" not in result.parsed


def test_implementer_role_schema() -> None:
    text = (
        '<output>{"modified_files": ["src/widget.py"], '
        '"implementation_notes": "Added retry logic"}</output>'
    )
    result = parse_node_output("implementer", [], text)
    assert result.success is True
    assert result.parsed == {
        "modified_files": ["src/widget.py"],
        "implementation_notes": "Added retry logic",
    }


def test_verifier_role_schema() -> None:
    text = (
        '<output>{"verification_status": "approved", '
        '"tests_passed": true, "last_test_output": "ok"}</output>'
    )
    result = parse_node_output("verifier", [], text)
    assert result.success is True
    assert result.parsed == {
        "verification_status": "approved",
        "tests_passed": True,
        "last_test_output": "ok",
    }


def test_field_outside_role_allowlist_is_rejected() -> None:
    """task_selector may only write selected_issue_ids — modifying files is verifier territory."""
    text = '<output>{"selected_issue_ids": [1], "modified_files": ["sneaky.py"]}</output>'
    result = parse_node_output("task_selector", [], text)
    assert result.success is False
    assert any("modified_files" in err for err in result.errors)


def test_invalid_type_is_rejected() -> None:
    """selected_issue_ids must be list[int]; string fails Pydantic validation."""
    text = '<output>{"selected_issue_ids": "not-a-list"}</output>'
    result = parse_node_output("task_selector", [], text)
    assert result.success is False
    assert any("selected_issue_ids" in err for err in result.errors)


def test_invalid_literal_is_rejected() -> None:
    """verification_status is a Literal; arbitrary strings fail."""
    text = '<output>{"verification_status": "maybe"}</output>'
    result = parse_node_output("verifier", [], text)
    assert result.success is False
    assert any("verification_status" in err for err in result.errors)


def test_missing_payload_is_descriptive_error() -> None:
    result = parse_node_output(
        "task_selector", [], "I have no idea what to pick."
    )
    assert result.success is False
    assert any("No JSON payload" in err for err in result.errors)


def test_invalid_json_is_descriptive_error() -> None:
    text = "<output>{this is not json}</output>"
    result = parse_node_output("task_selector", [], text)
    assert result.success is False
    assert any("not valid JSON" in err for err in result.errors)


def test_non_object_payload_is_rejected() -> None:
    text = "<output>[1, 2, 3]</output>"
    result = parse_node_output("task_selector", [], text)
    assert result.success is False
    assert any("must be an object" in err for err in result.errors)


def test_empty_output_skips_known_role_with_error() -> None:
    result = parse_node_output("task_selector", [], "")
    assert result.success is False
    assert any("No JSON payload" in err for err in result.errors)


def test_output_tag_wins_over_markdown_fence() -> None:
    """If both wrappers are present, <output> is canonical and wins."""
    text = (
        '```json\n{"selected_issue_ids": [9]}\n```\n<output>{"selected_issue_ids": [42]}</output>'
    )
    result = parse_node_output("task_selector", [], text)
    assert result.success is True
    assert result.parsed == {"selected_issue_ids": [42]}


# ---------------------------------------------------------------------------
# Per-agent output_schema (v0.5)
# ---------------------------------------------------------------------------


def test_per_agent_schema_overrides_role_default() -> None:
    """A custom output_schema narrows the contract beyond ROLE_FIELDS."""
    # verifier role normally writes 4 fields — we trim to one.
    text = '<output>{"verification_status": "approved", "tests_passed": true}</output>'
    result = parse_node_output("verifier", ["verification_status"], text)
    assert result.success is False  # tests_passed not declared by this agent
    assert any("tests_passed" in err for err in result.errors)


def test_per_agent_schema_lets_custom_role_be_validated() -> None:
    """Custom role + declared schema → validator runs (no fallback to skipped)."""
    text = '<output>{"modified_files": ["a.py", "b.py"]}</output>'
    result = parse_node_output("my-custom-role", ["modified_files"], text)
    assert result.success is True
    assert result.skipped is False  # schema declared, so we validate
    assert result.parsed == {"modified_files": ["a.py", "b.py"]}


def test_per_agent_schema_widens_role_default() -> None:
    """A schema can widen what a known role normally writes — still fully validated."""
    text = (
        '<output>{"selected_issue_ids": [7], '
        '"implementation_notes": "drove plan from picks"}</output>'
    )
    result = parse_node_output(
        "task_selector",
        ["selected_issue_ids", "implementation_notes"],
        text,
    )
    assert result.success is True
    assert result.parsed == {
        "selected_issue_ids": [7],
        "implementation_notes": "drove plan from picks",
    }


def test_empty_schema_falls_back_to_role_fields() -> None:
    """No schema set → behaviour identical to v0.4 (ROLE_FIELDS dispatch)."""
    text = '<output>{"selected_issue_ids": [1]}</output>'
    result = parse_node_output("task_selector", [], text)
    assert result.success is True
    assert result.parsed == {"selected_issue_ids": [1]}


def test_stale_field_name_falls_back_to_skipped() -> None:
    """A schema referencing a renamed/removed PipelineState field shouldn't crash.

    Hand-edited or stale rows can carry a name that no longer exists.
    The validator returns ``None`` and the parser reports skipped so
    the run keeps moving via permissive structured-merge.
    """
    result = parse_node_output(
        "custom-role",
        ["this_field_does_not_exist"],
        '<output>{"foo": "bar"}</output>',
    )
    assert result.success is True
    assert result.skipped is True
