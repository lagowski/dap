"""dap project — sub-commands for project-scoped pipeline operations.

Current pipeline types: cortex
"""

from __future__ import annotations

from typing import Annotated

import typer

from dap_cli.commands import cortex as _cortex
from dap_cli.paths import DEFAULT_ENGINE_PORT

project_app = typer.Typer(
    name="project",
    help="Project-scoped pipeline operations",
    no_args_is_help=True,
    add_completion=False,
)

_ENGINE_DEFAULT = f"http://localhost:{DEFAULT_ENGINE_PORT}"

# Reused across every sub-command — the engine requires a bearer token
# (JWT or opaque ``dap_*`` API token) on all endpoints except ``/health``.
_TokenOption = Annotated[
    str | None,
    typer.Option(
        "--token",
        help="DAP engine API token (or set DAP_AUTH_TOKEN)",
        envvar="DAP_AUTH_TOKEN",
    ),
]


@project_app.command("run")
def cmd_run(
    pipeline: Annotated[str, typer.Argument(help="Pipeline type (currently: cortex)")],
    issue_url: Annotated[str, typer.Argument(help="GitHub issue URL to process")],
    engine: Annotated[
        str,
        typer.Option("--engine", help="DAP engine base URL", envvar="DAP_ENGINE_URL"),
    ] = _ENGINE_DEFAULT,
    no_interactive: Annotated[
        bool,
        typer.Option("--no-interactive", help="Approve all gates automatically"),
    ] = False,
    watch: Annotated[
        bool,
        typer.Option("--watch", help="Monitor only — do not approve gates"),
    ] = False,
    workspace: Annotated[
        str | None,
        typer.Option("--workspace", help="Local workspace path for the pipeline"),
    ] = None,
    progress: Annotated[
        bool,
        typer.Option(
            "--progress/--no-progress",
            help="Show live per-node progress during the run",
        ),
    ] = True,
    token: _TokenOption = None,
) -> None:
    """Trigger a pipeline run for a GitHub issue.

    Example:

        dap project run cortex https://github.com/Dixter999/cortex-project/issues/332
    """
    if pipeline != "cortex":
        typer.echo(f"Unknown pipeline type: {pipeline!r}. Supported: cortex", err=True)
        raise typer.Exit(1)
    _cortex.cortex_run(
        issue_url=issue_url,
        engine_url=engine,
        no_interactive=no_interactive,
        watch=watch,
        workspace=workspace,
        token=token,
        show_progress=progress,
    )


@project_app.command("approve")
def cmd_approve(
    pipeline: Annotated[str, typer.Argument(help="Pipeline type (currently: cortex)")],
    run_id: Annotated[str, typer.Argument(help="Run ID to approve")],
    engine: Annotated[
        str,
        typer.Option("--engine", help="DAP engine base URL", envvar="DAP_ENGINE_URL"),
    ] = _ENGINE_DEFAULT,
    token: _TokenOption = None,
) -> None:
    """Approve the current gate for a paused run.

    Example:

        dap project approve cortex <run-id>
    """
    if pipeline != "cortex":
        typer.echo(f"Unknown pipeline type: {pipeline!r}. Supported: cortex", err=True)
        raise typer.Exit(1)
    _cortex.cortex_approve(run_id=run_id, engine_url=engine, token=token)


@project_app.command("reject")
def cmd_reject(
    pipeline: Annotated[str, typer.Argument(help="Pipeline type (currently: cortex)")],
    run_id: Annotated[str, typer.Argument(help="Run ID to reject")],
    reason: Annotated[str, typer.Argument(help="Rejection reason")] = "",
    engine: Annotated[
        str,
        typer.Option("--engine", help="DAP engine base URL", envvar="DAP_ENGINE_URL"),
    ] = _ENGINE_DEFAULT,
    token: _TokenOption = None,
) -> None:
    """Reject (abort) the current gate for a paused run.

    Example:

        dap project reject cortex <run-id> "Phase 1 spec incomplete"
    """
    if pipeline != "cortex":
        typer.echo(f"Unknown pipeline type: {pipeline!r}. Supported: cortex", err=True)
        raise typer.Exit(1)
    _cortex.cortex_reject(run_id=run_id, reason=reason, engine_url=engine, token=token)


@project_app.command("state")
def cmd_state(
    pipeline: Annotated[str, typer.Argument(help="Pipeline type (currently: cortex)")],
    run_id: Annotated[str, typer.Argument(help="Run ID to inspect")],
    engine: Annotated[
        str,
        typer.Option("--engine", help="DAP engine base URL", envvar="DAP_ENGINE_URL"),
    ] = _ENGINE_DEFAULT,
    fmt: Annotated[
        str,
        typer.Option(
            "--format",
            help="Output format: table (default) or json",
            click_type=__import__("click").Choice(["table", "json"], case_sensitive=False),
        ),
    ] = "table",
    token: _TokenOption = None,
) -> None:
    """Show current state of a pipeline run.

    Example:

        dap project state cortex <run-id>
        dap project state cortex <run-id> --format json
    """
    if pipeline != "cortex":
        typer.echo(f"Unknown pipeline type: {pipeline!r}. Supported: cortex", err=True)
        raise typer.Exit(1)
    _cortex.cortex_state(run_id=run_id, engine_url=engine, fmt=fmt, token=token)
