"""``dap mcp`` (#1046): the stdio MCP server, driven by the official MCP client.

Each test spawns ``python -m dap_cli mcp --headless`` as a subprocess and talks
to it over stdin/stdout with ``mcp.client.stdio``. The server runs a throwaway
in-process engine (temp SQLite, ASGI transport): no port, no network.
"""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXAMPLE_BUNDLE = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "python-func-pipeline"
    / "example.pipeline-bundle.json"
)

TOOLS = {
    "list_pipelines",
    "describe_pipeline",
    "run_pipeline",
    "run_status",
    "approve_gate",
    "reject_gate",
}

# Upper-case so the example's ``shout`` node (which upper-cases the greeting)
# writes the very same value into the state a second time.
SENTINEL = "SENTINEL" + secrets.token_hex(12).upper()
TOKEN_SENTINEL = "TOKENSENTINEL" + secrets.token_hex(12).upper()

_DEADLINE_S = 120.0


def _params(cwd: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "dap_cli", "mcp", "--headless"],
        cwd=str(cwd),
        env={
            # A secret the server process holds, and an API token: neither may
            # come back in a tool result even when a run's state carries it.
            "DAP_MCP_TEST_SECRET": SENTINEL,
            "DAP_AUTH_TOKEN": TOKEN_SENTINEL,
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )


class _Recorder:
    """Call tools and keep every raw result text, to scan for leaked secrets."""

    def __init__(self, session: ClientSession) -> None:
        self.session = session
        self.texts: list[str] = []

    async def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        result = await self.session.call_tool(name, arguments)
        text = "".join(getattr(block, "text", "") for block in getattr(result, "content", []))
        self.texts.append(text)
        self.texts.append(json.dumps(getattr(result, "structured_content", None), default=str))
        assert not getattr(result, "is_error", False), text
        data: dict[str, Any] = json.loads(text)
        return data

    async def wait_for(self, run_id: str, *, statuses: set[str]) -> dict[str, Any]:
        with anyio.fail_after(_DEADLINE_S):
            while True:
                status = await self.call("run_status", run_id=run_id)
                if status["final_status"] in statuses:
                    return status
                await anyio.sleep(0.2)

    def assert_no_secret(self) -> None:
        for text in self.texts:
            assert SENTINEL not in text, text
            assert TOKEN_SENTINEL not in text, text


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    path = tmp_path / "cwd"
    path.mkdir()
    return path


async def test_lists_tools_and_runs_the_example_headless_to_completion(cwd: Path) -> None:
    async with (
        stdio_client(_params(cwd)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        assert {tool.name for tool in listed.tools} == TOOLS

        rec = _Recorder(session)
        started = await rec.call(
            "run_pipeline",
            bundle_path=str(EXAMPLE_BUNDLE),
            initial_state={"extensions": {"name": SENTINEL, "token": TOKEN_SENTINEL}},
        )
        run_id = started["run_id"]
        pipeline_id = started["pipeline_id"]

        paused = await rec.wait_for(run_id, statuses={"paused", "success", "failed", "aborted"})
        assert paused["final_status"] == "paused", paused
        assert paused["paused_at_node"] == "gate", paused
        # The state is returned, with the secret values scrubbed.
        assert "[REDACTED:DAP_MCP_TEST_SECRET]" in json.dumps(paused["state"]), paused

        approved = await rec.call("approve_gate", run_id=run_id, node_id="gate")
        assert approved["run_id"] == run_id

        done = await rec.wait_for(run_id, statuses={"success", "failed", "aborted"})
        assert done["final_status"] == "success", done
        assert {done["node_statuses"][n] for n in ("greet", "shout", "gate")} == {"success"}

        pipelines = await rec.call("list_pipelines")
        assert pipeline_id in {p["id"] for p in pipelines["pipelines"]}

        described = await rec.call("describe_pipeline", pipeline_id=pipeline_id)
        assert [n["id"] for n in described["nodes"]][:2] == ["greet", "shout"]
        assert described["gates"] == ["gate"]

        rerun = await rec.call("run_pipeline", pipeline_id=pipeline_id)
        assert rerun["pipeline_id"] == pipeline_id

        rec.assert_no_secret()
    assert list(cwd.iterdir()) == []  # nothing written to the caller's cwd


async def test_reject_gate_aborts_the_run(cwd: Path) -> None:
    async with (
        stdio_client(_params(cwd)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        rec = _Recorder(session)
        started = await rec.call("run_pipeline", bundle_path=str(EXAMPLE_BUNDLE))
        run_id = started["run_id"]
        await rec.wait_for(run_id, statuses={"paused"})

        rejected = await rec.call("reject_gate", run_id=run_id, node_id="gate")
        assert rejected["run_id"] == run_id

        done = await rec.wait_for(run_id, statuses={"success", "failed", "aborted"})
        assert done["final_status"] == "aborted", done
        assert done["node_statuses"].get("gate") != "success", done


async def test_a_refused_call_is_an_error_result_without_secrets(cwd: Path) -> None:
    async with (
        stdio_client(_params(cwd)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("run_status", {"run_id": f"no-such-run-{SENTINEL}"})
        assert result.is_error
        text = "".join(getattr(block, "text", "") for block in result.content)
        assert "404" in text, text
        assert SENTINEL not in text, text
