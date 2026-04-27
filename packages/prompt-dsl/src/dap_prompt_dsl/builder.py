"""Render Jinja2 template with context, validate XML output."""

from __future__ import annotations

import re
from typing import Any

from jinja2 import StrictUndefined, TemplateSyntaxError, UndefinedError
from jinja2.exceptions import TemplateError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ConfigDict

from dap_prompt_dsl.validator import DEFAULT_ROOT, validate_xml

# StrictUndefined formats its message as ``'<name>' is undefined`` for
# the simple top-level case we're trying to surface. Anything more
# exotic (attribute access, etc.) keeps the generic hint.
_UNDEFINED_NAME_PATTERN = re.compile(r"'([^']+)' is undefined")


class PromptBuildError(Exception):
    """Raised when template rendering fails (Jinja syntax / undefined variable)."""


class BuildResult(BaseModel):
    """Result of prompt build operation."""

    model_config = ConfigDict(extra="forbid")

    xml: str
    valid: bool
    warnings: list[str] = []
    errors: list[str] = []


def _make_sandbox() -> SandboxedEnvironment:
    """Sandboxed Jinja2 — blocks attribute access to dunder methods, file I/O, etc.

    StrictUndefined turns missing context fields into errors instead of silent
    empty strings — surfaces problems early.
    """
    return SandboxedEnvironment(
        autoescape=False,  # XML output, we rely on validator
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )


def build_prompt(
    template: str,
    context: dict[str, Any],
    *,
    expected_root: str = DEFAULT_ROOT,
    input_schema: list[str] | None = None,
) -> BuildResult:
    """Render template with context, validate XML output.

    Pure function: same input → same output, no side effects.

    When ``input_schema`` is supplied and non-empty, the render context
    is projected to that subset — only fields the agent has declared as
    inputs are visible to the template. A reference to anything else
    fails with a descriptive ``PromptBuildError`` pointing at the
    schema as the fix. When ``input_schema`` is ``None`` or empty, the
    context is passed through unchanged (backward compat with v0.4
    agents that haven't declared a contract yet).

    Raises:
        PromptBuildError: when template fails to render (syntax error,
                          undefined variable in StrictUndefined mode).

    Returns:
        BuildResult with rendered xml + validation outcome. The XML is returned
        even if invalid (so callers can show diagnostics), but `valid=False`.
    """
    env = _make_sandbox()
    render_context = _project_context(context, input_schema)

    try:
        compiled = env.from_string(template)
        rendered = compiled.render(**render_context)
    except TemplateSyntaxError as exc:
        raise PromptBuildError(f"Template syntax error: {exc.message}") from exc
    except UndefinedError as exc:
        raise PromptBuildError(
            _format_undefined_error(exc, input_schema, render_context),
        ) from exc
    except TemplateError as exc:
        raise PromptBuildError(f"Template error: {exc}") from exc

    outcome = validate_xml(rendered, expected_root=expected_root)

    return BuildResult(
        xml=rendered,
        valid=outcome.valid,
        warnings=outcome.warnings,
        errors=outcome.errors,
    )


def _project_context(
    context: dict[str, Any],
    input_schema: list[str] | None,
) -> dict[str, Any]:
    """Restrict render context to declared input fields when schema is set.

    Empty or ``None`` schema → pass-through (backward compat with agents
    that pre-date #58). Anything not in ``input_schema`` is dropped so a
    template referencing it triggers the standard
    :class:`jinja2.UndefinedError` from ``StrictUndefined``.
    """
    if not input_schema:
        return context
    return {name: context[name] for name in input_schema if name in context}


def _format_undefined_error(
    exc: UndefinedError,
    input_schema: list[str] | None,
    render_context: dict[str, Any],
) -> str:
    """Build a descriptive message for ``UndefinedError`` in StrictUndefined mode.

    Two distinct failure modes when ``input_schema`` is set:

    1. Template references a field the agent didn't declare → fix is
       to extend ``input_schema`` (or remove the reference).
    2. Field IS declared but the caller didn't supply it in the
       context → fix is to add it to the runtime/preview payload, not
       to touch ``input_schema``.

    We extract the offending name from the Jinja message and pick the
    right hint. Falls back to the generic schema reminder when the
    name can't be parsed (exotic message shapes from attribute access,
    etc.).
    """
    base = f"Template references undefined variable: {exc.message}"
    if not input_schema:
        return base

    declared = ", ".join(input_schema)
    name = _extract_undefined_name(exc.message)

    if name is not None and name in input_schema and name not in render_context:
        # Declared, but caller didn't supply it.
        return (
            f"{base}. The field is declared in agent.input_schema but no "
            f"value was supplied — pass it in the render context."
        )

    # Either the name isn't in the schema, or we couldn't parse the
    # message — both point at the schema as the place to edit.
    return (
        f"{base}. Add the field to agent.input_schema or update the "
        f"template. Currently declared inputs: {declared}."
    )


def _extract_undefined_name(message: str | None) -> str | None:
    """Pull the offending name out of a StrictUndefined error message."""
    if not message:
        return None
    match = _UNDEFINED_NAME_PATTERN.search(message)
    return match.group(1) if match else None
