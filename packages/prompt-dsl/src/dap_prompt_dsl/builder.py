"""Render Jinja2 template with context, validate XML output."""

from __future__ import annotations

from typing import Any

from jinja2 import StrictUndefined, TemplateSyntaxError, UndefinedError
from jinja2.exceptions import TemplateError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ConfigDict

from dap_prompt_dsl.validator import DEFAULT_ROOT, validate_xml


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
) -> BuildResult:
    """Render template with context, validate XML output.

    Pure function: same input → same output, no side effects.

    Raises:
        PromptBuildError: when template fails to render (syntax error,
                          undefined variable in StrictUndefined mode).

    Returns:
        BuildResult with rendered xml + validation outcome. The XML is returned
        even if invalid (so callers can show diagnostics), but `valid=False`.
    """
    env = _make_sandbox()

    try:
        compiled = env.from_string(template)
        rendered = compiled.render(**context)
    except TemplateSyntaxError as exc:
        raise PromptBuildError(f"Template syntax error: {exc.message}") from exc
    except UndefinedError as exc:
        raise PromptBuildError(f"Template references undefined variable: {exc.message}") from exc
    except TemplateError as exc:
        raise PromptBuildError(f"Template error: {exc}") from exc

    outcome = validate_xml(rendered, expected_root=expected_root)

    return BuildResult(
        xml=rendered,
        valid=outcome.valid,
        warnings=outcome.warnings,
        errors=outcome.errors,
    )
