"""Repository layer — public façade re-exporting per-entity helpers (#253).

Implementation lives in per-entity modules under
``dap_engine.persistence``:

- :mod:`dap_engine.persistence.agents`
- :mod:`dap_engine.persistence.pipelines`
- :mod:`dap_engine.persistence.projects`
- :mod:`dap_engine.persistence.runs`
- :mod:`dap_engine.persistence._common` (shared ``NotFoundError`` /
  id / timestamp helpers)

Existing callers continue using ``from dap_engine.persistence import
repository as repo`` and call ``repo.foo()`` — the names below
preserve that surface so the split is transparent at the import
layer.
"""

from __future__ import annotations

from dap_engine.persistence._common import NotFoundError
from dap_engine.persistence.agents import (
    archive_agent,
    count_pipelines_using_agents,
    create_agent,
    get_agent,
    get_agent_template,
    get_agent_version,
    get_agents_by_ids,
    list_agent_versions,
    list_agents,
    pipelines_using_agent,
    update_agent,
)
from dap_engine.persistence.batch_runs import (
    append_batch_result,
    create_batch_run,
    finalize_batch_run,
    get_batch_run,
)
from dap_engine.persistence.env_vars import (
    delete_env_var_by_key,
    find_env_vars_by_keys,
    get_env_var_by_key,
    list_env_vars,
)
from dap_engine.persistence.pipelines import (
    archive_pipeline,
    create_pipeline,
    get_pipeline,
    get_pipeline_version,
    list_pipeline_versions,
    list_pipelines,
    update_pipeline,
    update_pipeline_ui_metadata,
)
from dap_engine.persistence.projects import (
    archive_project,
    create_project,
    get_project,
    list_projects,
    update_project,
)
from dap_engine.persistence.runs import (
    create_run,
    finalize_run,
    get_run,
    get_run_node_log,
    get_run_state,
    list_run_node_logs,
    list_run_state_history,
    list_runs,
    mark_expired_gate_runs_as_failed,
    mark_stale_running_runs_as_failed,
    pause_run,
    try_claim_resume,
    try_claim_revive,
)

__all__ = [
    "NotFoundError",
    "append_batch_result",
    "archive_agent",
    "archive_pipeline",
    "archive_project",
    "count_pipelines_using_agents",
    "create_agent",
    "create_batch_run",
    "create_pipeline",
    "create_project",
    "create_run",
    "delete_env_var_by_key",
    "finalize_batch_run",
    "finalize_run",
    "find_env_vars_by_keys",
    "get_agent",
    "get_agent_template",
    "get_agent_version",
    "get_agents_by_ids",
    "get_batch_run",
    "get_env_var_by_key",
    "get_pipeline",
    "get_pipeline_version",
    "get_project",
    "get_run",
    "get_run_node_log",
    "get_run_state",
    "list_agent_versions",
    "list_agents",
    "list_env_vars",
    "list_pipeline_versions",
    "list_pipelines",
    "list_projects",
    "list_run_node_logs",
    "list_run_state_history",
    "list_runs",
    "mark_expired_gate_runs_as_failed",
    "mark_stale_running_runs_as_failed",
    "pause_run",
    "pipelines_using_agent",
    "try_claim_resume",
    "try_claim_revive",
    "update_agent",
    "update_pipeline",
    "update_pipeline_ui_metadata",
    "update_project",
]
