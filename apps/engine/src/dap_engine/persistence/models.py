"""SQLAlchemy 2.0 ORM models.

Versioning model:
- `agents` and `pipelines` hold stable identity and a pointer to the current version.
- `agent_versions` and `pipeline_versions` hold the full immutable configuration per
  version. Updating an agent/pipeline always creates a new version row; previous
  versions remain unchanged so runs can reference exact configurations forever.
"""

from dap_engine.persistence.agent_models import AgentORM, AgentVersionORM
from dap_engine.persistence.auth_models import (
    ApiTokenORM,
    AuditLogORM,
    OAuthAccountORM,
    UserORM,
)
from dap_engine.persistence.model_base import Base
from dap_engine.persistence.pipeline_models import PipelineORM, PipelineVersionORM
from dap_engine.persistence.project_models import ProjectORM
from dap_engine.persistence.run_models import (
    BatchRunORM,
    NodeExecutionLogORM,
    NodeOutputChunkORM,
    RunORM,
    StateSnapshotORM,
)
from dap_engine.persistence.settings_models import InstanceEnvVarORM

__all__ = [
    "AgentORM",
    "AgentVersionORM",
    "ApiTokenORM",
    "AuditLogORM",
    "Base",
    "BatchRunORM",
    "InstanceEnvVarORM",
    "NodeExecutionLogORM",
    "NodeOutputChunkORM",
    "OAuthAccountORM",
    "PipelineORM",
    "PipelineVersionORM",
    "ProjectORM",
    "RunORM",
    "StateSnapshotORM",
    "UserORM",
]
