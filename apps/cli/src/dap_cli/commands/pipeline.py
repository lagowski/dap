"""dap pipeline — operations on pipeline bundle files (export, run)."""

from __future__ import annotations

import contextlib
import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any, Protocol

import httpx
import typer

from dap_cli.hub_export import IO_ICONS, write_hub_bundle
from dap_cli.paths import DEFAULT_ENGINE_PORT

# ``dap pipeline run`` exit codes (documented in docs/usage.md).
EXIT_SUCCESS = 0
EXIT_FAILED = 1  # a node failed, or the run ended failed/aborted
EXIT_USAGE = 2  # bad arguments or a bundle the engine refused
EXIT_GATE = 3  # stopped at a human-approval gate (no --approve-gates)
EXIT_TIMEOUT = 4  # the run did not finish within --timeout

_TERMINAL = frozenset({"success", "failed", "aborted"})
_POLL_INTERVAL_S = 0.2

pipeline_app = typer.Typer(
    name="pipeline",
    help="Operations on pipeline bundle files (export, run)",
    no_args_is_help=True,
    add_completion=False,
)


@pipeline_app.command("export")
def cmd_export(
    bundle: Annotated[
        Path, typer.Argument(help="A *.pipeline-bundle.json file", exists=True, dir_okay=False)
    ],
    hub: Annotated[
        str, typer.Option("--hub", help="Hub page slug: writes a Labs hub pipeline bundle")
    ],
    tagline5: Annotated[
        str,
        typer.Option(
            "--tagline5", help="Hub card line: 3-6 words, at most 60 characters, plain text"
        ),
    ],
    io: Annotated[
        str,
        typer.Option(
            "--io",
            help="Hub card icons <in>:<out>, from: " + " ".join(sorted(IO_ICONS)),
        ),
    ],
    out: Annotated[
        Path, typer.Option("--out", help="Directory that receives <slug>/page/page.json")
    ] = Path("."),
    public_names: Annotated[
        Path | None,
        typer.Option(
            "--public-names",
            help="A public-names.yaml (group -> list of names) that extends the built-in "
            "allowlist of the client-name scrubber",
            dir_okay=False,
        ),
    ] = None,
    examples: Annotated[
        Path | None,
        typer.Option(
            "--examples",
            help="A JSON file of hand-written synthetic node examples: {<node id>: {input, "
            'output, example_source: "synthetic"}}',
            dir_okay=False,
        ),
    ] = None,
) -> None:
    """Write a hub tutorial bundle (with its read-only graph) for one pipeline bundle."""
    try:
        target = write_hub_bundle(
            bundle,
            hub,
            out,
            tagline5=tagline5,
            io=io,
            public_names=public_names,
            examples=examples,
        )
    except ValueError as exc:  # Unsafe is a ValueError: a refusal, a bad slug, tagline or icon
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"wrote {target}")


class _EngineClient(Protocol):
    """What the run driver needs: ``httpx.Client`` (a live engine) and the in-process
    ``TestClient`` (``--headless``; Starlette builds it on ``httpx2``) both fit."""

    def get(self, url: str) -> Any: ...

    def post(self, url: str, *, json: Any = None) -> Any: ...


class _RunError(Exception):
    """A refusal from the engine: printed, then exit ``EXIT_USAGE``."""


def _parse_state(pairs: list[str]) -> dict[str, Any]:
    """``KEY=VALUE`` pairs -> ``initial_state.extensions``. A JSON value is decoded."""
    extensions: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep or not key.strip():
            raise _RunError(f"--state expects KEY=VALUE, got {pair!r}")
        try:
            value: Any = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        extensions[key.strip()] = value
    return {"extensions": extensions} if extensions else {}


def _check(response: Any, what: str) -> Any:
    if response.is_error:
        try:
            detail: Any = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise _RunError(f"{what} failed: HTTP {response.status_code}: {detail}")
    return response.json()


@contextlib.contextmanager
def _bundle_dir_on_path(bundle: Path) -> Iterator[None]:
    """Let a python-func package that sits next to the bundle import (in-process only)."""
    entry = str(bundle.resolve().parent)
    sys.path.insert(0, entry)
    try:
        yield
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(entry)


def _emit(line: str, *, quiet: bool) -> None:
    # With --json, progress goes to stderr so stdout stays one JSON document.
    typer.echo(line, err=quiet)


def _drive_run(
    client: _EngineClient,
    bundle_payload: dict[str, Any],
    *,
    initial_state: dict[str, Any],
    approve_gates: bool,
    timeout_s: float,
    as_json: bool,
) -> tuple[int, dict[str, Any]]:
    """Import the bundle, trigger a run, stream node progress; return (exit code, final)."""
    pipeline = _check(client.post("/pipelines/import", json=bundle_payload), "bundle import")
    run = _check(
        client.post("/runs", json={"pipeline_id": pipeline["id"], "initial_state": initial_state}),
        "run trigger",
    )
    run_id = str(run["id"])
    _emit(f"run {run_id} started (pipeline {pipeline['name']!r})", quiet=as_json)

    seen: dict[str, str] = {}
    deadline = time.monotonic() + timeout_s
    approved: set[str] = set()
    while True:
        run = _check(client.get(f"/runs/{run_id}"), "run read")
        for log in _check(client.get(f"/runs/{run_id}/nodes"), "node log read"):
            node, status = str(log["node_id"]), str(log["status"])
            key = f"{node}:{log['id']}"
            if seen.get(key) == status:
                continue
            seen[key] = status
            line = f"  {node:<20} {status}"
            if status != "running":
                line += f"  cost ${float(log.get('cost_usd') or 0):.4f}"
                line += f"  {int(log.get('duration_ms') or 0)} ms"
            if status == "failed" and log.get("error_message"):
                line += f"  error: {str(log['error_message']).splitlines()[0][:200]}"
            _emit(line, quiet=as_json)

        status = str(run["final_status"])
        if status in _TERMINAL:
            break
        if status == "paused" and run.get("paused_at_node"):
            gate = str(run["paused_at_node"])
            if not approve_gates:
                _emit(
                    f"run {run_id} stopped at gate {gate!r} (exit {EXIT_GATE}). "
                    "Re-run with --approve-gates to approve gates automatically, or against "
                    f"a running engine approve it with: POST /runs/{run_id}/nodes/{gate}/approve",
                    quiet=as_json,
                )
                return EXIT_GATE, run
            if gate not in approved:
                _check(client.post(f"/runs/{run_id}/nodes/{gate}/approve"), f"approve {gate}")
                approved.add(gate)
                _emit(f"  {gate:<20} approved (--approve-gates)", quiet=as_json)
        if time.monotonic() > deadline:
            _emit(f"run {run_id} did not finish within {timeout_s:g}s", quiet=as_json)
            return EXIT_TIMEOUT, run
        time.sleep(_POLL_INTERVAL_S)

    failed = sorted(n for n, s in (run.get("node_statuses") or {}).items() if s == "failed")
    if status == "success" and not failed:
        _emit(f"run {run_id} success", quiet=as_json)
        return EXIT_SUCCESS, run
    names = f" (failed node: {', '.join(failed)})" if failed else ""
    reason = f": {run['failure_reason']}" if run.get("failure_reason") else ""
    _emit(f"run {run_id} {status}{names}{reason}", quiet=as_json)
    return EXIT_FAILED, run


def _final_document(client: _EngineClient, run: dict[str, Any], code: int) -> dict[str, Any]:
    run_id = str(run["id"])
    state: Any = None
    with contextlib.suppress(_RunError):
        state = _check(client.get(f"/runs/{run_id}/state"), "state read")
    return {"exit_code": code, "run": run, "state": state}


@pipeline_app.command("run")
def cmd_run(
    bundle: Annotated[
        Path, typer.Argument(help="A *.pipeline-bundle.json file", exists=True, dir_okay=False)
    ],
    headless: Annotated[
        bool,
        typer.Option(
            "--headless",
            help="Run on a throwaway in-process engine (temp SQLite, no server, no dashboard)",
        ),
    ] = False,
    state: Annotated[
        list[str] | None,
        typer.Option("--state", help="KEY=VALUE put in initial_state.extensions (repeatable)"),
    ] = None,
    approve_gates: Annotated[
        bool, typer.Option("--approve-gates", help="Approve every human-approval gate")
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the final run and state as JSON on stdout")
    ] = False,
    timeout: Annotated[
        float, typer.Option("--timeout", help="Seconds to wait for the run to finish")
    ] = 3600.0,
    engine: Annotated[
        str,
        typer.Option(
            "--engine", help="Engine base URL (without --headless)", envvar="DAP_ENGINE_URL"
        ),
    ] = f"http://localhost:{DEFAULT_ENGINE_PORT}",
    token: Annotated[
        str | None,
        typer.Option(
            "--token", help="Engine API token (without --headless)", envvar="DAP_AUTH_TOKEN"
        ),
    ] = None,
) -> None:
    """Import a bundle, run it to the end and exit with the run's outcome.

    Exit codes: 0 success, 1 a node failed, 2 bad input, 3 stopped at a
    gate (no --approve-gates), 4 timed out.
    """
    try:
        payload = json.loads(bundle.read_text(encoding="utf-8"))
        initial_state = _parse_state(state or [])
    except (OSError, json.JSONDecodeError, _RunError) as exc:
        typer.echo(f"cannot read {bundle}: {exc}", err=True)
        raise typer.Exit(EXIT_USAGE) from exc

    with contextlib.ExitStack() as stack:
        client: _EngineClient
        if headless:
            from dap_cli.bootstrap import headless_engine  # noqa: PLC0415  (engine import is heavy)

            stack.enter_context(_bundle_dir_on_path(bundle))
            client = stack.enter_context(headless_engine())
        else:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            client = stack.enter_context(httpx.Client(base_url=engine, headers=headers))
        try:
            code, run = _drive_run(
                client,
                payload,
                initial_state=initial_state,
                approve_gates=approve_gates,
                timeout_s=timeout,
                as_json=as_json,
            )
        except (_RunError, httpx.HTTPError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(EXIT_USAGE) from exc
        if as_json:
            typer.echo(json.dumps(_final_document(client, run, code), indent=2, default=str))
    raise typer.Exit(code)
