"""dap mcp: a stdio MCP server that exposes DAP pipelines as tools (#1046).

An agent CLI (Claude Code, Cursor, ...) starts ``dap mcp`` as a subprocess and
calls these tools over stdin/stdout:

- ``list_pipelines``: the pipelines on the engine.
- ``describe_pipeline``: one pipeline's nodes, edges, gates and inputs.
- ``run_pipeline``: start a run from a pipeline id or a bundle file; returns the run id.
- ``run_status``: a run's status, per-node statuses and state.
- ``approve_gate`` (only with ``--allow-approve``) / ``reject_gate``: decide a
  human-approval gate.

The server talks to a running engine (``--engine`` / ``DAP_ENGINE_URL`` and
``--token`` / ``DAP_AUTH_TOKEN``, as ``dap pipeline run`` does) or, with
``--headless``, to a throwaway in-process engine that lives as long as the
server. Every tool result and error message goes through the engine's
redaction (:func:`dap_engine.redaction.redact`): the value of any environment
variable of this process, and the API token, come back as ``[REDACTED:<NAME>]``.
Agent prompt templates are never returned.

The server is driven by a model, so it trusts none of its arguments:

- ``bundle_path`` is refused unless ``--allow-bundle-path DIR`` is given, and the
  file must resolve inside ``DIR``. Only then does its directory go on
  ``sys.path`` (headless python-func packages), since that runs code in a
  process that holds the environment's secrets.
- ``approve_gate`` exists only with ``--allow-approve``: without it the agent
  cannot approve its own human gate.
- Ids are validated as slugs before they go into an engine URL, so ``../agents``
  cannot steer the authenticated client to another route.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx

from dap_cli.commands.pipeline import _bundle_dir_on_path, _check, _EngineClient, _RunError

# An environment value shorter than this is not treated as a secret: scrubbing
# "1" or "on" everywhere would wreck every result.
_MIN_SECRET_LEN = 4

# Flag-like values, which are no secret however long, and which would otherwise
# scrub every "true" out of a run's state.
_NOT_SECRET_VALUES = frozenset({"true", "false", "none", "null", "yes", "no", "on", "off"})

# A pipeline, run or node id: a UUID or a slug. No "/", no ".", so an id cannot
# leave the route it is put into.
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")

# Variables whose values are not secrets and are routinely part of legitimate
# results (paths). Everything else in the environment is scrubbed.
_NOT_SECRET = frozenset(
    {
        "PATH",
        "HOME",
        "PWD",
        "OLDPWD",
        "SHELL",
        "USER",
        "LOGNAME",
        "LANG",
        "TERM",
        "TMPDIR",
        "TMP",
        "TEMP",
        "VIRTUAL_ENV",
        "DAP_ENGINE_URL",
    }
)
_NOT_SECRET_PREFIXES = ("LC_", "XDG_")

_RUN_FIELDS = (
    "id",
    "pipeline_id",
    "pipeline_version",
    "final_status",
    "paused_at_node",
    "node_statuses",
    "failure_reason",
    "total_cost_usd",
    "started_at",
    "finished_at",
)


def collect_secrets(environ: Mapping[str, str], token: str | None) -> dict[str, str]:
    """``NAME -> value`` for every value a tool result must never contain."""
    found: dict[str, str] = {}
    for name, value in environ.items():
        if name in _NOT_SECRET or name.startswith(_NOT_SECRET_PREFIXES):
            continue
        if len(value) >= _MIN_SECRET_LEN and value.lower() not in _NOT_SECRET_VALUES:
            found[name] = value
    if token:
        found["DAP_AUTH_TOKEN"] = token
    return found


class Redactor:
    """Scrub known secret values (and secret-shaped tokens) from any JSON value."""

    def __init__(self, known: Mapping[str, str]) -> None:
        from dap_engine.redaction import redact  # noqa: PLC0415  (engine import is heavy)

        self._redact = redact
        self._known = dict(known)

    def add(self, name: str, value: str) -> None:
        if value:
            self._known[name] = value

    def text(self, value: str) -> str:
        return self._redact(value, known_secrets=self._known)

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            return {self.text(str(k)): self.value(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self.value(v) for v in value]
        return value


def _id(value: str, what: str) -> str:
    """``value`` if it is a safe id to put into an engine URL, else a refusal."""
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise _RunError(f"invalid {what} {value!r}: expected a UUID or a slug ([A-Za-z0-9_-])")
    return value


class _LockedClient:
    """Serialise engine calls: tools run in worker threads, the clients are not
    meant for concurrent use."""

    def __init__(self, client: _EngineClient) -> None:
        self._client = client
        self._lock = threading.Lock()

    def get(self, url: str) -> Any:
        with self._lock:
            return self._client.get(url)

    def post(self, url: str, *, json: Any = None) -> Any:
        with self._lock:
            return self._client.post(url, json=json)


class DapTools:
    """The tool implementations, independent of the MCP transport."""

    def __init__(
        self,
        client: _EngineClient,
        *,
        stack: contextlib.ExitStack,
        bundle_root: Path | None = None,
    ) -> None:
        self._client = client
        self._stack = stack
        self._bundle_root = bundle_root.expanduser().resolve() if bundle_root else None
        self._paths: set[Path] = set()

    def list_pipelines(self) -> dict[str, Any]:
        page = _check(self._client.get("/pipelines?limit=500"), "pipeline list")
        return {
            "pipelines": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "description": p.get("description", ""),
                    "version": p.get("version"),
                }
                for p in page.get("items", [])
            ],
            "total": page.get("total"),
        }

    def describe_pipeline(self, pipeline_id: str) -> dict[str, Any]:
        pipeline_id = _id(pipeline_id, "pipeline_id")
        pipeline = _check(self._client.get(f"/pipelines/{pipeline_id}"), "pipeline read")
        nodes: list[dict[str, Any]] = []
        needed: set[str] = set()
        produced: set[str] = set()
        for node in pipeline.get("nodes", []):
            entry: dict[str, Any] = {"id": node["id"], "agent_id": node["agent_id"]}
            agent = self._client.get(f"/agents/{_id(str(node['agent_id']), 'agent_id')}")
            if not agent.is_error:
                data = agent.json()
                entry |= {
                    "agent_name": data.get("name"),
                    "runtime": data.get("runtime_id"),
                    "inputs": data.get("input_schema", []),
                    "outputs": data.get("output_schema", []),
                }
                needed.update(data.get("input_schema", []))
                produced.update(data.get("output_schema", []))
            nodes.append(entry)
        defaults = pipeline.get("defaults") or {}
        return {
            "id": pipeline["id"],
            "name": pipeline["name"],
            "description": pipeline.get("description", ""),
            "version": pipeline.get("version"),
            "entry_point": pipeline.get("entry_point"),
            "nodes": nodes,
            "edges": [
                {k: e[k] for k in ("source", "target", "condition") if e.get(k) is not None}
                for e in pipeline.get("edges", [])
            ],
            "gates": list(defaults.get("approval_required_nodes", [])),
            # State keys some node reads and no node writes: the caller supplies them.
            "inputs": sorted(needed - produced),
            "initial_state_hint": {"extensions": {key: "..." for key in sorted(needed - produced)}},
        }

    def run_pipeline(
        self,
        pipeline_id: str | None = None,
        bundle_path: str | None = None,
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if bool(pipeline_id) == bool(bundle_path):
            raise _RunError("pass exactly one of pipeline_id or bundle_path")
        if bundle_path:
            bundle = self._allowed_bundle(bundle_path)
            try:
                payload = json.loads(bundle.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise _RunError(f"cannot read bundle {bundle}: {exc}") from exc
            self._import_path(bundle)
            pipeline = _check(self._client.post("/pipelines/import", json=payload), "bundle import")
            pipeline_id = str(pipeline["id"])
        pipeline_id = _id(str(pipeline_id), "pipeline_id")
        run = _check(
            self._client.post(
                "/runs", json={"pipeline_id": pipeline_id, "initial_state": initial_state or {}}
            ),
            "run trigger",
        )
        return {"run_id": str(run["id"]), "pipeline_id": pipeline_id, "status": run["final_status"]}

    def run_status(self, run_id: str) -> dict[str, Any]:
        run_id = _id(run_id, "run_id")
        run = _check(self._client.get(f"/runs/{run_id}"), "run read")
        result: dict[str, Any] = {k: run.get(k) for k in _RUN_FIELDS}
        result["run_id"] = result.pop("id")
        state: Any = None
        with contextlib.suppress(_RunError):
            state = _check(self._client.get(f"/runs/{run_id}/state"), "state read")
        result["state"] = state
        return result

    def approve_gate(self, run_id: str, node_id: str) -> dict[str, Any]:
        run_id, node_id = self._paused_at(run_id, node_id)
        run = _check(
            self._client.post(f"/runs/{run_id}/nodes/{node_id}/approve"), f"approve {node_id}"
        )
        return {"run_id": str(run["id"]), "node_id": node_id, "status": run["final_status"]}

    def reject_gate(self, run_id: str, node_id: str) -> dict[str, Any]:
        run_id, node_id = self._paused_at(run_id, node_id)
        aborted = _check(self._client.post(f"/runs/{run_id}/abort"), f"reject {node_id}")
        return {"run_id": str(aborted["id"]), "node_id": node_id, "status": aborted["final_status"]}

    def _paused_at(self, run_id: str, node_id: str) -> tuple[str, str]:
        """The validated ids, if the run is paused at exactly this gate; else a refusal."""
        run_id = _id(run_id, "run_id")
        node_id = _id(node_id, "node_id")
        run = _check(self._client.get(f"/runs/{run_id}"), "run read")
        if run.get("final_status") != "paused" or run.get("paused_at_node") != node_id:
            raise _RunError(
                f"run {run_id} is not waiting at gate {node_id!r} "
                f"(status {run.get('final_status')}, paused at {run.get('paused_at_node')!r})"
            )
        return run_id, node_id

    def _allowed_bundle(self, bundle_path: str) -> Path:
        """The resolved bundle file, only inside ``--allow-bundle-path``."""
        if self._bundle_root is None:
            raise _RunError(
                "bundle_path is disabled: start the server with --allow-bundle-path DIR "
                "to run bundle files from DIR, or pass a pipeline_id"
            )
        bundle = Path(bundle_path).expanduser().resolve()
        if not bundle.is_relative_to(self._bundle_root):
            raise _RunError(f"bundle_path must be inside {self._bundle_root}")
        return bundle

    def _import_path(self, bundle: Path) -> None:
        """Headless: let a python-func package next to the bundle import, for the
        server's lifetime (the run executes after this call returns). Harmless
        against a remote engine, which imports nothing in this process."""
        parent = bundle.resolve().parent
        if parent not in self._paths:
            self._paths.add(parent)
            self._stack.enter_context(_bundle_dir_on_path(bundle))


def build_server(tools: DapTools, redactor: Redactor, *, allow_approve: bool = False) -> Any:
    """The MCP server: every tool result and error message is redacted.

    ``approve_gate`` is registered only with ``allow_approve``."""
    from mcp.server.mcpserver import MCPServer  # noqa: PLC0415
    from mcp.server.mcpserver.exceptions import ToolError  # noqa: PLC0415

    from dap_cli import __version__  # noqa: PLC0415

    server = MCPServer(
        name="dap",
        version=__version__,
        instructions=(
            "Run DAP deterministic agent pipelines. list_pipelines / describe_pipeline to "
            "find one, run_pipeline to start it, run_status to follow it, reject_gate (or "
            "approve_gate, when the server allows it) when it waits at a human-approval gate."
        ),
    )

    def guarded(call: Any) -> dict[str, Any]:
        try:
            result: dict[str, Any] = redactor.value(call())
        except Exception as exc:
            message = (
                str(exc)
                if isinstance(exc, _RunError | httpx.HTTPError)
                else (f"{type(exc).__name__}: {exc}")
            )
            raise ToolError(redactor.text(message)) from None
        return result

    @server.tool()
    def list_pipelines() -> dict[str, Any]:
        """List the pipelines on the engine (id, name, description, version)."""
        return guarded(tools.list_pipelines)

    @server.tool()
    def describe_pipeline(pipeline_id: str) -> dict[str, Any]:
        """Describe one pipeline: its nodes (agent, runtime, inputs, outputs), edges,
        human-approval gates and the state keys a run needs in initial_state.extensions."""
        return guarded(lambda: tools.describe_pipeline(pipeline_id))

    @server.tool()
    def run_pipeline(
        pipeline_id: str | None = None,
        bundle_path: str | None = None,
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a run of a pipeline (by id) or of a *.pipeline-bundle.json file (imported
        first). initial_state is e.g. {"extensions": {"name": "Ada"}}. Returns the run id;
        follow it with run_status."""
        return guarded(lambda: tools.run_pipeline(pipeline_id, bundle_path, initial_state))

    @server.tool()
    def run_status(run_id: str) -> dict[str, Any]:
        """A run's final_status (running, paused, success, failed, aborted), the gate it
        waits at (paused_at_node), per-node statuses, cost and current state."""
        return guarded(lambda: tools.run_status(run_id))

    if allow_approve:

        @server.tool()
        def approve_gate(run_id: str, node_id: str) -> dict[str, Any]:
            """Approve the human-approval gate node_id of a paused run; the run continues."""
            return guarded(lambda: tools.approve_gate(run_id, node_id))

    @server.tool()
    def reject_gate(run_id: str, node_id: str) -> dict[str, Any]:
        """Reject the human-approval gate node_id of a paused run; the run is aborted."""
        return guarded(lambda: tools.reject_gate(run_id, node_id))

    return server


@contextlib.contextmanager
def _engine_client(
    *,
    headless: bool,
    engine: str,
    token: str | None,
    redactor: Redactor,
    transport: httpx.BaseTransport | None = None,
) -> Iterator[tuple[_EngineClient, contextlib.ExitStack]]:
    with contextlib.ExitStack() as stack:
        client: _EngineClient
        if headless:
            from dap_cli.bootstrap import headless_engine  # noqa: PLC0415

            test_client = stack.enter_context(headless_engine())
            redactor.add("HEADLESS_AUTH", test_client.headers.get("Authorization", ""))
            client = test_client
        else:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            client = stack.enter_context(
                httpx.Client(base_url=engine, headers=headers, transport=transport)
            )
        yield _LockedClient(client), stack


def mcp_command(
    *,
    headless: bool,
    engine: str,
    token: str | None,
    allow_bundle_path: Path | None = None,
    allow_approve: bool = False,
) -> None:
    """Serve the DAP tools over stdio until the client closes the connection."""
    redactor = Redactor(collect_secrets(os.environ, token))
    with _engine_client(headless=headless, engine=engine, token=token, redactor=redactor) as (
        client,
        stack,
    ):
        tools = DapTools(client, stack=stack, bundle_root=allow_bundle_path)
        build_server(tools, redactor, allow_approve=allow_approve).run("stdio")
