"""``dap demo`` (#1042): one command, zero keys, a running pipeline.

The command boots a throwaway engine on a temp SQLite database, bootstraps a
demo admin, imports the example python-func bundle shipped as dap-cli package
data and runs it. ``--no-dashboard`` approves the gate and exits; without it the
run stays paused at the gate while the engine and the dashboard serve it.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from dap_cli.__main__ import app
from typer.testing import CliRunner

REPO = Path(__file__).resolve().parents[2]
CLI_PROJECT = REPO / "apps" / "cli"
DEMO_FILES = (
    "dap_cli/_demo/example.pipeline-bundle.json",
    "dap_cli/_demo/examplepipe/__init__.py",
    "dap_cli/_demo/examplepipe/nodes.py",
    "dap_cli/_demo/examplepipe/gates.py",
)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Path]]:
    """Empty cwd, empty HOME, private TMPDIR, no DAP_* env; forget examplepipe."""
    dirs = {name: tmp_path / name for name in ("cwd", "home", "tmp")}
    for path in dirs.values():
        path.mkdir()
    monkeypatch.chdir(dirs["cwd"])
    monkeypatch.setenv("HOME", str(dirs["home"]))
    monkeypatch.setenv("TMPDIR", str(dirs["tmp"]))
    monkeypatch.setattr("tempfile.tempdir", str(dirs["tmp"]))
    for key in list(os.environ):
        if key.startswith("DAP_"):
            monkeypatch.delenv(key)
    before = set(sys.modules)
    yield dirs
    for name in set(sys.modules) - before:
        if name.split(".")[0] == "examplepipe":
            del sys.modules[name]


def _password(out: str) -> str:
    match = re.search(r"password:\s+(\S+)", out)
    assert match, out
    return match.group(1)


def _assert_nothing_left(dirs: dict[str, Path]) -> None:
    """No state, no log file: cwd, HOME and TMPDIR are empty again."""
    for name, path in dirs.items():
        assert list(path.iterdir()) == [], f"{name} not empty: {list(path.iterdir())}"


def test_demo_no_dashboard_runs_end_to_end(
    sandbox: dict[str, Path], caplog: pytest.LogCaptureFixture
) -> None:
    result = CliRunner().invoke(app, ["demo", "--no-dashboard"])
    out = result.stdout + result.stderr
    assert result.exit_code == 0, out
    for node in ("greet", "shout", "gate"):
        assert f"{node:<20} success" in out, out
    assert "greet → shout → gate → end completed" in out, out
    password = _password(out)
    assert out.count(password) == 1, "the demo password is printed exactly once"
    assert all(password not in record.getMessage() for record in caplog.records)
    _assert_nothing_left(sandbox)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_demo_with_dashboard_prints_the_run_url_and_stays_at_the_gate(
    sandbox: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from dap_cli.commands import demo

    engine_port, dashboard_port = _free_port(), _free_port()
    seen: dict[str, Any] = {}

    class FakeDashboard:
        def poll(self) -> int:
            return 0

    def fake_spawn(*, port: int, engine_url: str) -> FakeDashboard:
        seen["spawn"] = (port, engine_url)
        return FakeDashboard()

    def ctrl_c(session: demo.DemoSession) -> None:
        # While the command waits: the run is paused at the gate on the live engine.
        with httpx.Client(base_url=session.engine_url, headers=session.auth_headers) as c:
            run = c.get(f"/runs/{session.run_id}").json()
        seen["run"] = run
        seen["state_dir"] = session.state_dir
        assert session.state_dir.exists()
        raise KeyboardInterrupt

    monkeypatch.setattr(demo, "spawn_dashboard", fake_spawn)
    monkeypatch.setattr(demo, "_wait_for_interrupt", ctrl_c)
    result = CliRunner().invoke(
        app, ["demo", "--port", str(dashboard_port), "--engine-port", str(engine_port)]
    )
    out = result.stdout + result.stderr
    assert result.exit_code == 0, out
    assert seen["spawn"] == (dashboard_port, f"http://127.0.0.1:{engine_port}")
    run = seen["run"]
    assert run["final_status"] == "paused" and run["paused_at_node"] == "gate", run
    assert f"http://127.0.0.1:{dashboard_port}/runs/{run['id']}" in out, out
    password = _password(out)
    assert out.count(password) == 1
    assert not seen["state_dir"].exists(), "Ctrl-C removes the temp dir"
    _assert_nothing_left(sandbox)


def test_demo_assets_resolve_and_ship_in_the_wheel(tmp_path: Path) -> None:
    from dap_cli.commands.demo import demo_bundle

    with demo_bundle() as bundle:
        assert bundle.name == "example.pipeline-bundle.json" and bundle.is_file()
        assert (bundle.parent / "examplepipe" / "nodes.py").is_file()

    uv = shutil.which("uv")
    assert uv, "uv is needed to build the dap-cli wheel"
    # Release path (sdist, then a wheel built from it) and a direct wheel build.
    for args in (["--no-sources"], ["--wheel"]):
        out_dir = tmp_path / args[0].strip("-")
        subprocess.run(
            [uv, "build", *args, "--out-dir", str(out_dir), str(CLI_PROJECT)],
            check=True,
            capture_output=True,
        )
        (wheel,) = out_dir.glob("dap_cli-*.whl")
        names = set(zipfile.ZipFile(wheel).namelist())
        missing = [f for f in DEMO_FILES if f not in names]
        assert not missing, f"{wheel.name} lacks {missing}"
        assert not any("__pycache__" in n for n in names if n.startswith("dap_cli/_demo/"))
