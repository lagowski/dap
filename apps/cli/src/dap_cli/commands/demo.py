"""dap demo — one command, zero keys: a real pipeline run on a throwaway instance (#1042).

Everything lives in a temporary directory that is removed on exit (also on
Ctrl-C): the SQLite database, nothing else. A demo admin is bootstrapped with a
generated password that is printed once to the terminal and never logged. The
example python-func bundle (greet -> shout -> gate) ships as package data of
dap-cli, so ``pipx run dap-cli demo`` needs no checkout, API key or network.
"""

from __future__ import annotations

import contextlib
import json
import secrets
import signal
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http import HTTPStatus
from importlib import resources
from pathlib import Path
from typing import Any

import httpx
import typer

from dap_cli.commands.pipeline import (
    EXIT_GATE,
    EXIT_SUCCESS,
    _bundle_dir_on_path,
    _drive_run,
    _EngineClient,
    _RunError,
)
from dap_cli.dashboard import find_bundle, find_node, spawn_dashboard

DEMO_ADMIN_EMAIL = "demo@dap.invalid"  # reserved TLD: never a real mailbox
BUNDLE_NAME = "example.pipeline-bundle.json"
_ENGINE_START_TIMEOUT_S = 30.0
_RUN_TIMEOUT_S = 120.0


@contextlib.contextmanager
def demo_bundle() -> Iterator[Path]:
    """Yield the example bundle; its ``examplepipe`` package sits next to it.

    Installed wheels carry it as ``dap_cli/_demo`` (see apps/cli/hatch_build.py);
    an editable checkout falls back to ``examples/python-func-pipeline``.
    """
    packaged = resources.files("dap_cli") / "_demo"
    if packaged.joinpath(BUNDLE_NAME).is_file():
        with resources.as_file(packaged) as path:
            yield path / BUNDLE_NAME
        return
    checkout = Path(__file__).resolve().parents[5] / "examples" / "python-func-pipeline"
    if not (checkout / BUNDLE_NAME).is_file():
        raise FileNotFoundError("the demo bundle is missing from this dap-cli install")
    yield checkout / BUNDLE_NAME


@dataclass
class DemoSession:
    """The live demo, while the command waits for Ctrl-C."""

    state_dir: Path
    engine_url: str
    run_id: str
    auth_headers: dict[str, str] = field(repr=False)


def _login(client: Any, password: str) -> dict[str, str]:
    login = client.post(
        "/auth/jwt/login", data={"username": DEMO_ADMIN_EMAIL, "password": password}
    )
    if login.status_code != HTTPStatus.OK:
        raise _RunError(f"demo admin login failed: HTTP {login.status_code}")
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def _wait_for_interrupt(session: DemoSession) -> None:
    """Block until Ctrl-C or SIGTERM (raises ``KeyboardInterrupt``). Patched in tests."""
    signal.signal(signal.SIGTERM, _interrupt)  # ``kill`` cleans up like Ctrl-C
    while True:
        time.sleep(1)


@contextlib.contextmanager
def _serve_engine(app: Any, port: int) -> Iterator[str]:
    """Serve the engine with uvicorn in a background thread; yield its base URL."""
    import uvicorn  # noqa: PLC0415

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    )
    thread = threading.Thread(target=server.run, name="dap-demo-engine", daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + _ENGINE_START_TIMEOUT_S
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise _RunError(f"the demo engine did not start on port {port}")
            time.sleep(0.05)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def demo_command(*, no_dashboard: bool, port: int, engine_port: int) -> None:
    from dap_engine.app import EngineConfig, create_app  # noqa: PLC0415  (heavy import)

    from dap_cli.bootstrap import ensure_admin_user, generate_password  # noqa: PLC0415

    try:
        with contextlib.ExitStack() as stack:
            state_dir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="dap-demo-")))
            bundle = stack.enter_context(demo_bundle())
            payload = json.loads(bundle.read_text(encoding="utf-8"))
            stack.enter_context(_bundle_dir_on_path(bundle))

            db_path = state_dir / "state.db"
            password = generate_password()
            ensure_admin_user(db_path, email=DEMO_ADMIN_EMAIL, password=password)
            typer.echo("DAP demo: a throwaway instance (removed when this command exits)")
            # The only place the password ever appears: this terminal.
            typer.echo(f"  demo admin  email: {DEMO_ADMIN_EMAIL}  password: {password}")
            config = EngineConfig(
                db_path=str(db_path),
                host="127.0.0.1",
                port=engine_port,
                auth_jwt_secret=secrets.token_urlsafe(32),
            )
            engine_app = create_app(config)

            if no_dashboard:
                from fastapi.testclient import TestClient  # noqa: PLC0415

                client: _EngineClient = stack.enter_context(TestClient(engine_app))
                client.headers.update(_login(client, password))  # type: ignore[attr-defined]
                code, _ = _drive_run(
                    client,
                    payload,
                    initial_state={},
                    approve_gates=True,
                    timeout_s=_RUN_TIMEOUT_S,
                    as_json=False,
                )
                if code == EXIT_SUCCESS:
                    typer.echo("greet → shout → gate → end completed")
                raise typer.Exit(code)

            engine_url = stack.enter_context(_serve_engine(engine_app, engine_port))
            http = stack.enter_context(httpx.Client(base_url=engine_url))
            headers = _login(http, password)
            http.headers.update(headers)
            code, run = _drive_run(
                http,
                payload,
                initial_state={},
                approve_gates=False,
                timeout_s=_RUN_TIMEOUT_S,
                as_json=False,
            )
            if code != EXIT_GATE:
                raise typer.Exit(code)
            run_id = str(run["id"])
            _start_dashboard(stack, port=port, engine_url=engine_url, run_id=run_id)
            typer.echo("Press Ctrl+C to stop and remove the demo instance.")
            session = DemoSession(state_dir, engine_url, run_id, headers)
            with contextlib.suppress(KeyboardInterrupt):
                _wait_for_interrupt(session)
        typer.echo("demo stopped; temporary instance removed")
    except KeyboardInterrupt:
        typer.echo("demo interrupted; temporary instance removed", err=True)
        raise typer.Exit(130) from None
    except (_RunError, httpx.HTTPError, FileNotFoundError) as exc:
        typer.echo(f"dap demo failed: {exc}", err=True)
        raise typer.Exit(1) from exc


def _start_dashboard(
    stack: contextlib.ExitStack[Any], *, port: int, engine_url: str, run_id: str
) -> None:
    """Spawn the bundled dashboard (stopped on exit) and say where the run's gate is."""
    dashboard = spawn_dashboard(port=port, engine_url=engine_url)
    if dashboard is not None:
        stack.callback(_stop, dashboard)
        typer.echo(f"  dashboard   http://127.0.0.1:{port}  (log in as the demo admin)")
        typer.echo(f"  approve the gate here: http://127.0.0.1:{port}/runs/{run_id}")
        return
    if find_bundle() is None:
        why = "no dashboard bundle in this install"
    elif find_node() is None:
        why = "Node.js is not on PATH"
    else:
        why = "it failed to start"
    typer.echo(f"  dashboard   not started ({why}); the engine is at {engine_url}")
    typer.echo(f"  approve the gate: POST {engine_url}/runs/{run_id}/nodes/gate/approve")


def _stop(proc: Any) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
