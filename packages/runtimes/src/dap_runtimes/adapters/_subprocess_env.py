"""Shared env-layering for runtime adapters (#65, #388).

Used by ``bash`` / ``claude-code`` / ``codex`` / ``gemini-cli`` (subprocess)
and ``python-func`` (in-process) so all adapters apply identical layering:

1. Engine process env (lowest) — secrets like ``ANTHROPIC_API_KEY``.
2. Instance ``env_vars`` (overlay) — operator-set shared defaults
   (e.g. GitHub tokens) configured via ``/settings/admin/env-vars``
   (#388).
3. Project ``env_vars`` (overlay) — non-secret per-project values.
4. Per-agent ``runtime_config.env`` (highest) — last-mile override.

``compute_env_overlay`` returns layers 2-4 only (the delta), which the
in-process ``python-func`` adapter patches onto ``os.environ`` for the
duration of the callable. ``merge_subprocess_env`` layers that delta on
top of a full ``os.environ`` copy for subprocess adapters.
"""

from __future__ import annotations

import os
from typing import Any


def compute_env_overlay(
    project_env_vars: dict[str, str],
    runtime_config: dict[str, Any],
    *,
    instance_env_vars: dict[str, str] | None = None,
) -> tuple[dict[str, str], str | None]:
    """Build the env *overlay* (layers 2-4) — instance < project < runtime.

    Returns ``(overlay, error)``:

    - ``overlay`` is the merged delta and **excludes** ``os.environ`` — it
      contains only the keys explicitly supplied by the three overlay
      layers. The in-process adapter relies on this so it knows exactly
      which keys to set and restore around a callable.
    - ``error`` is ``None`` on success, or a descriptive message when
      ``runtime_config.env`` is malformed (not a ``dict[str, str]``). On
      error the partial overlay built so far is still returned, but callers
      must check ``error`` before using it.

    Precedence matches the subprocess contract: rightmost layer wins
    (per-agent ``runtime_config.env`` over project over instance).
    """
    overlay: dict[str, str] = {}
    if instance_env_vars:
        overlay.update(instance_env_vars)
    if project_env_vars:
        overlay.update(project_env_vars)

    extra = runtime_config.get("env")
    if extra is None:
        return (overlay, None)
    if not isinstance(extra, dict):
        return (overlay, "runtime_config.env must be a dict[str, str]")
    for key, value in extra.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return (
                overlay,
                "runtime_config.env must be a dict[str, str]; "
                f"got element of type {type(key).__name__}={type(value).__name__}",
            )
        overlay[key] = value
    return (overlay, None)


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
    overlay, error = compute_env_overlay(
        project_env_vars,
        runtime_config,
        instance_env_vars=instance_env_vars,
    )
    env.update(overlay)
    return (env, error)
