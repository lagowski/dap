# dap-schemas

Shared Pydantic v2 schemas for the [DAP](https://github.com/lagowski/dap)
(Deterministic Agent Pipeline) ecosystem. This package defines the data
contract every other first-party package uses to exchange agents,
pipelines, runs, and runtime tasks.

> **Distribution vs. import name.** The PyPI distribution is
> `dap-schemas`; the Python module remains `dap_types`. Analogous to
> `Pillow` → `from PIL import Image`. The discrepancy is intentional —
> the original short name was squatted on PyPI when the project went
> public.

## What's in here

| Schema | Lives in `dap_types.` | Purpose |
|---|---|---|
| `Agent` + `AgentRole` | `agent` | Versioned agent definition: runtime, prompt template, input/output schema, role enum |
| `Pipeline` + `PipelineNode` / `PipelineEdge` / `PipelineDefaults` + `EdgeCondition` (`ComparisonCondition`, `LogicalCondition`) | `pipeline` | DAG of agent nodes + edges + entry point + run defaults |
| `PipelineState` + `StateSnapshot` + `FinalStatus` / `VerificationStatus` | `state` | Execution state passed between nodes; every agent's `input_schema` / `output_schema` references fields here |
| `Run` + `NodeExecutionLog` + `NodeStatus` | `run` | A single execution of a pipeline, with status, started_at, final_status, cost |
| `Project` + `RECOMMENDED_PIPELINE_KINDS` | `project` | Workspace layer — binds workflow kinds to pipelines, env-var overrides |
| `RuntimeAdapter` (Protocol) + `RuntimeKind` + `HealthStatus` | `runtime` | The interface every adapter in `dap-runtimes` implements |
| `RuntimeTask` / `RuntimeResult` | `runtime` | I/O shape exchanged between the engine and runtime adapters |
| `agent_output_model` / `role_output_model` / `resolve_output_validator` / `ROLE_FIELDS` | `role_outputs` | Helpers for building Pydantic validators against an agent's declared `output_schema` |

Audit, user, and API-token schemas live in the engine package
(`dap_engine.persistence.models`) rather than here — they don't
need to cross the wire to third-party consumers.

All models use `model_config = ConfigDict(extra="forbid")` — unknown
fields are rejected on parse, so a typo in a pipeline export is caught
at import time rather than silently ignored.

## Installation

You usually install this transitively via `dap-cli`:

```bash
pipx install dap-cli   # pulls dap-engine + dap-runtimes + dap-schemas + dap-prompt-dsl + dap-cortex
```

Standalone (e.g. if you're writing a third-party tool that exchanges
DAP payloads):

```bash
pip install dap-schemas
```

## Usage

```python
from pathlib import Path
import json
from dap_types import Agent, Pipeline, PipelineState

# Parse a pipeline bundle from JSON.
bundle = json.loads(Path("my-pipeline.json").read_text())
pipeline = Pipeline.model_validate(bundle["pipeline"])

# PipelineState is the canonical execution state — every agent
# reads/writes a subset of its fields.
state = PipelineState(
    run_id="run-abc",
    repo="me/my-project",
    branch="feature/foo",
    description="Sketch a 5-step plan for issue #42",
)
```

The full schema catalogue is enforced by the engine on every write:
a `Pipeline` with an unknown node `agent_id`, a malformed
`PipelineState` reference in an agent's `output_schema`, etc., is
rejected with HTTP 422 carrying a Pydantic error trace pinpointing the
offending field.

## Compatibility

- Python 3.13+
- Pydantic 2.9+
- Semantic versioning. Breaking schema changes bump the major version
  and ship a migration in
  [`apps/engine/src/dap_engine/persistence/migrations.py`](https://github.com/lagowski/dap/blob/main/apps/engine/src/dap_engine/persistence/migrations.py).

## See also

- [DAP project README](https://github.com/lagowski/dap) — architecture overview and quick-start.
- [`docs/architecture.md`](https://github.com/lagowski/dap/blob/main/docs/architecture.md) — full schema relationships and the LangGraph state machine that consumes them.

## License

See the [main repository](https://github.com/lagowski/dap) for licensing details.
