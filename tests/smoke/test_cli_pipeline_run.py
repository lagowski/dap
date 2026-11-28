"""``dap pipeline run <bundle> --headless`` (#1041).

Each test runs the real CLI command, which boots a throwaway engine in-process
(temp SQLite, ASGI transport: no port, no network), imports the bundle, runs it
and exits with the run's outcome.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from dap_cli.__main__ import app
from typer.testing import CliRunner

EXAMPLE_BUNDLE = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "python-func-pipeline"
    / "example.pipeline-bundle.json"
)

# Documented in docs/usage.md.
EXIT_FAILED = 1
EXIT_GATE = 3


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Run from an empty cwd and forget any module a bundle imported."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    before = set(sys.modules)
    yield cwd
    for name in set(sys.modules) - before:
        if name.split(".")[0] in {"examplepipe", "boompipe"}:
            del sys.modules[name]


def _run(*args: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(app, ["pipeline", "run", *args])
    return result.exit_code, result.stdout, result.stderr


def test_headless_run_with_approve_gates_succeeds_and_leaves_no_state(_isolated: Path) -> None:
    code, out, err = _run(str(EXAMPLE_BUNDLE), "--headless", "--approve-gates")
    assert code == 0, out + err
    for node in ("greet", "shout", "gate"):
        assert f"{node:<20} success" in out, out
    assert "success" in out.splitlines()[-1]
    assert list(_isolated.iterdir()) == []  # no .dap/ in the cwd


def test_headless_run_without_approve_gates_stops_at_the_gate() -> None:
    code, out, err = _run(str(EXAMPLE_BUNDLE), "--headless")
    assert code == EXIT_GATE, out + err
    assert "stopped at gate 'gate'" in out
    assert "--approve-gates" in out
    assert "gate                 success" not in out


def test_json_prints_the_final_run_state() -> None:
    code, out, err = _run(
        str(EXAMPLE_BUNDLE), "--headless", "--approve-gates", "--json", "--state", "name=CI"
    )
    assert code == 0, out + err
    doc = json.loads(out)  # progress went to stderr: stdout is one JSON document
    assert doc["exit_code"] == 0
    assert doc["run"]["final_status"] == "success"
    assert doc["run"]["node_statuses"] == {
        "greet": "success",
        "shout": "success",
        "gate": "success",
    }
    assert doc["state"]["extensions"]["greeting_loud"] == "HELLO, CI!"
    assert "greet" in err


def test_a_node_that_raises_exits_non_zero_and_names_the_node(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "boom"
    (bundle_dir / "boompipe").mkdir(parents=True)
    (bundle_dir / "boompipe" / "__init__.py").write_text("")
    (bundle_dir / "boompipe" / "nodes.py").write_text(
        "async def ok(state, config):\n"
        "    return {}\n\n"
        "async def explode(state, config):\n"
        "    raise RuntimeError('kaboom')\n"
    )
    bundle = json.loads(EXAMPLE_BUNDLE.read_text())
    bundle["pipeline"]["name"] = "Boom pipeline"
    bundle["pipeline"]["defaults"]["approval_required_nodes"] = []
    agents = bundle["bundled_agents"]
    agents["ex_greet"]["runtime_config"] = {"callable_path": "boompipe.nodes:ok"}
    agents["ex_shout"]["runtime_config"] = {"callable_path": "boompipe.nodes:explode"}
    agents["ex_gate"]["runtime_config"] = {"callable_path": "boompipe.nodes:ok"}
    path = bundle_dir / "boom.pipeline-bundle.json"
    path.write_text(json.dumps(bundle))

    code, out, err = _run(str(path), "--headless")
    assert code == EXIT_FAILED, out + err
    assert "shout                failed" in out
    assert "failed node: shout" in out.splitlines()[-1]


def test_bad_state_pair_is_a_usage_error() -> None:
    code, _, err = _run(str(EXAMPLE_BUNDLE), "--headless", "--state", "novalue")
    assert code == 2
    assert "KEY=VALUE" in err
