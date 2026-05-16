"""Shared subprocess-env builder for runtime adapters (#65, #388).

Used by ``bash`` / ``claude-code`` / ``codex`` / ``gemini-cli`` so all
adapters apply identical layering:

1. Engine process env (lowest) — secrets like ``ANTHROPIC_API_KEY``.
2. Instance ``env_vars`` (overlay) — operator-set shared defaults
   (e.g. GitHub tokens) configured via ``/settings/admin/env-vars``
   (#388).
3. Project ``env_vars`` (overlay) — non-secret per-project values.
4. Per-agent ``runtime_config.env`` (highest) — last-mile override.
"""

from __future__ import annotations

import os
from typing import Any


def merge_subprocess_env(
    project_env_vars: dict[str, str],
    runtime_config: dict[str, Any],
    *,
    instance_env_vars: dict[str, str] | None = None,
) -> tuple[dict[str, str], str | None]:
    """Build a subprocess ``env`` dict per the four-layer contract.

    Returns ``(env, error)``:

    - ``env`` always populated, ready to pass to
      ``asyncio.create_subprocess_exec(..., env=env, ...)``.
    - ``error`` is ``None`` on success, or a descriptive message when
      ``runtime_config.env`` is malformed (not a ``dict[str, str]``).
      Callers map that to their adapter's failure shape.

    ``instance_env_vars`` is keyword-only so existing call sites that
    haven't been threaded through yet keep working (empty overlay).
    Layered above ``os.environ`` and below ``project_env_vars`` —
    operators who want a value forced into every project's runtime
    set it here once; per-project overrides still win on conflict.
    """
    env = os.environ.copy()
    if instance_env_vars:
        env.update(instance_env_vars)
    if project_env_vars:
        env.update(project_env_vars)

    extra = runtime_config.get("env")
    if extra is None:
        return (env, None)
    if not isinstance(extra, dict):
        return (env, "runtime_config.env must be a dict[str, str]")
    for key, value in extra.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return (
                env,
                "runtime_config.env must be a dict[str, str]; "
                f"got element of type {type(key).__name__}={type(value).__name__}",
            )
        env[key] = value
    return (env, None)
