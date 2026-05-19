"""Project — workspace abstraction owning a working directory + workflow bindings.

A Project composes existing Pipelines into a workflow: it carries the
context (working directory or remote repo, default branch, env vars)
and binds *workflow kinds* (configure / plan / develop / ...) to
specific pipeline IDs. Pipelines stay reusable; the project glues
context onto them.

The list ``RECOMMENDED_PIPELINE_KINDS`` is a UX hint, not an enforced
enum — the dashboard surfaces these slots first-class, but custom
keys are accepted at the API layer. That keeps users free to invent
new workflow phases (``release``, ``hotfix``, ``backfill``, …) without
a code change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, computed_field

RECOMMENDED_PIPELINE_KINDS: Final = (
    "configure",
    "plan",
    "develop",
    "verify",
    "release",
)


class Project(BaseModel):
    """A workspace + a binding of workflow kinds to pipelines."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str = ""

    # Either a local path the engine should run subprocesses in, or a
    # remote repo URL that the user clones manually before running. The
    # engine treats both as opaque strings — no validation, no auth — so
    # a single-user local-trust setup works without auth plumbing.
    working_directory: str | None = None
    repo_url: str | None = None
    default_branch: str = "main"

    # Workflow bindings. Keys are kinds (recommended or custom),
    # values are pipeline ids. The engine validates that every value
    # is an existing non-archived pipeline at write time.
    pipelines: dict[str, str] = Field(default_factory=dict)

    # Project-scoped env vars layered onto subprocess environments via
    # the bash / cli runtimes. Engine process env provides the base
    # layer, project ``env_vars`` override that, and per-agent
    # ``runtime_config.env`` wins over both. Secrets should stay in
    # engine env — these are convenience for non-sensitive values
    # like ``WORKSPACE_NAME`` or feature flags.
    env_vars: dict[str, str] = Field(default_factory=dict)

    # Node IDs that the engine auto-approves without waiting for a human
    # POST /runs/{id}/nodes/{node_id}/approve (#477). Applies to every run
    # triggered under this project. Per-run initial_state.extensions.auto_approve_nodes
    # overrides this list for that specific run.
    auto_approve_nodes: list[str] = Field(default_factory=list)

    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_active(self) -> bool:
        """Soft-delete flag — ``archived_at is None``.

        Exposed as a ``computed_field`` so it round-trips through
        ``model_dump`` / FastAPI responses for the dashboard.
        """
        return self.archived_at is None
