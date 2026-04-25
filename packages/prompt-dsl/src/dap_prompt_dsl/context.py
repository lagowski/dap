"""Typed context helpers for common agent roles.

These are convenience Pydantic models that match expected shapes for built-in
roles. Custom agents can pass any dict[str, Any] to build_prompt — these types
just provide structure + validation when the role matches.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PromptContextBase(BaseModel):
    """Base for typed contexts. Custom agents may extend this or pass dicts."""

    model_config = ConfigDict(extra="allow")

    role: str
    task: str
    constraints: list[str] = Field(default_factory=list)


class CodeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str
    framework: str | None = None
    existing_files: list[str] = Field(default_factory=list)


class UserStory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    acceptance_criteria: list[str] = Field(default_factory=list)


class OutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: str
    location: str | None = None


class TestAuthorContext(PromptContextBase):
    """Context expected by Test Authoring Agent."""

    user_story: UserStory
    code_context: CodeContext
    output: OutputSpec


class ImplementerContext(PromptContextBase):
    """Context for Implementation Agent (make tests green)."""

    failing_test_output: str
    allowed_files: list[str] = Field(default_factory=list)
    code_context: CodeContext | None = None


class VerifierContext(PromptContextBase):
    """Context for Verifier Agent (DoD check)."""

    modified_files: list[str]
    test_output: str
    coverage_report: str | None = None
