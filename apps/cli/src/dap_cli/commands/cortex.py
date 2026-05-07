"""Cortex pipeline operations — implementation behind `dap project run/approve/reject/state cortex`.

Calls the DAP engine REST API. Does NOT import from `cortex.*` directly —
the cortex package lives in packages/cortex/ (Fase 1) and is loaded
only via the bundle JSON that ships inside it.
"""

from __future__ import annotations

import importlib.resources
import json
import os
import re
import time
from typing import Any

import httpx
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

DEFAULT_ENGINE_URL = "http://localhost:7333"
POLL_INTERVAL_SECONDS = 10
CORTEX_BUNDLE_NAME = "cortex-full.pipeline-bundle.json"
CORTEX_PROJECT_NAME = "cortex"
CORTEX_PIPELINE_KIND = "full"

# Status colours for display
_STATUS_STYLE: dict[str, str] = {
    "running": "cyan",
    "paused": "yellow",
    "success": "green",
    "failed": "red",
    "aborted": "red",
}


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


def parse_issue_url(url: str) -> tuple[str, int]:
    """Parse a GitHub issue URL and return (owner/repo, issue_number).

    Accepts:
        https://github.com/Dixter999/cortex-project/issues/332
        https://github.com/owner/repo/issues/42
    """
    pattern = r"https?://github\.com/([^/]+/[^/]+)/issues/(\d+)"
    match = re.fullmatch(pattern, url.rstrip("/"))
    if not match:
        raise ValueError(
            f"Cannot parse GitHub issue URL: {url!r}\n"
            "Expected format: https://github.com/<owner>/<repo>/issues/<number>"
        )
    return match.group(1), int(match.group(2))


# ---------------------------------------------------------------------------
# Bundle loading
# ---------------------------------------------------------------------------


def load_cortex_bundle() -> dict[str, Any]:
    """Load cortex-full.pipeline-bundle.json from the installed cortex package.

    Requires `cortex` package (packages/cortex/) to be installed in the
    current Python environment. Raises ImportError with a clear message if not.
    """
    try:
        bundle_ref = importlib.resources.files("cortex.dap_bundles") / CORTEX_BUNDLE_NAME
        bundle_text = bundle_ref.read_text(encoding="utf-8")
    except (ModuleNotFoundError, FileNotFoundError) as exc:
        raise ImportError(
            "Could not load the Cortex pipeline bundle.\n\n"
            "The `cortex` package must be installed in this environment.\n"
            "Run: uv add --workspace cortex\n\n"
            "(This depends on packages/cortex/ being present in the DAP monorepo — "
            "see Etapa 2 / dap#170 for the migration status.)"
        ) from exc
    return json.loads(bundle_text)


# ---------------------------------------------------------------------------
# Engine health check
# ---------------------------------------------------------------------------


def _client(engine_url: str) -> httpx.Client:
    return httpx.Client(base_url=engine_url.rstrip("/"), timeout=10.0)


def check_engine(engine_url: str) -> None:
    """Raise SystemExit with a helpful message if the engine is unreachable."""
    try:
        with _client(engine_url) as client:
            resp = client.get("/health")
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        console.print(f"[red]✗ DAP engine not reachable at {engine_url}[/red]")
        console.print("[dim]  Start with: uv run dap-engine[/dim]")
        console.print(f"[dim]  Error: {exc}[/dim]")
        raise SystemExit(1) from exc


# ---------------------------------------------------------------------------
# Pipeline import — idempotent
# ---------------------------------------------------------------------------


def _find_pipeline_by_name(client: httpx.Client, name: str, version: str) -> str | None:
    """Return existing pipeline id if name+description matches, else None."""
    resp = client.get("/pipelines", params={"limit": 200})
    resp.raise_for_status()
    for p in resp.json().get("items", []):
        if p.get("name") == name:
            return str(p["id"])
    return None


def ensure_pipeline_imported(engine_url: str, bundle: dict[str, Any]) -> str:
    """Import the Cortex bundle into the engine if not already present.

    Returns the pipeline_id (existing or freshly created).
    """
    pipeline_payload = bundle.get("pipeline", {})
    pipeline_name: str = pipeline_payload.get("name", "cortex-full")

    # Strip keys that are not part of PipelineImportRequest
    import_body = {
        "schema_version": bundle.get("schema_version", "pipeline-export/1"),
        "pipeline": pipeline_payload,
    }
    if "bundled_agents" in bundle:
        import_body["bundled_agents"] = bundle["bundled_agents"]

    with _client(engine_url) as client:
        existing_id = _find_pipeline_by_name(client, pipeline_name, "")
        if existing_id:
            console.print(
                f"[dim]  Pipeline '{pipeline_name}' already imported — id {existing_id}[/dim]"
            )
            return existing_id

        console.print(f"[cyan]→ Importing pipeline '{pipeline_name}'...[/cyan]")
        resp = client.post("/pipelines/import", json=import_body)
        if resp.status_code not in (200, 201):
            console.print(f"[red]✗ Failed to import pipeline: {resp.status_code}[/red]")
            console.print(f"[dim]{resp.text}[/dim]")
            raise SystemExit(1)
        pipeline_id = str(resp.json()["id"])
        console.print(f"[green]✓ Pipeline imported — id {pipeline_id}[/green]")
        return pipeline_id


# ---------------------------------------------------------------------------
# Project — create or reuse
# ---------------------------------------------------------------------------


def _collect_env_vars() -> dict[str, str]:
    """Collect Cortex-relevant env vars from the current process environment."""
    keys = [
        "GH_TOKEN_READ",
        "GH_TOKEN_ISSUES",
        "GH_TOKEN_CODE",
        "GH_TOKEN_MERGE",
        "CORTEX_DATABASE_URL",
        "DAP_DATABASE_URL",
    ]
    return {k: v for k in keys if (v := os.environ.get(k))}


def ensure_project(engine_url: str, pipeline_id: str, workspace_path: str) -> str:
    """Create the 'cortex' project if it doesn't exist. Returns project_id."""
    with _client(engine_url) as client:
        # Look for existing project named 'cortex'
        resp = client.get("/projects", params={"limit": 200})
        resp.raise_for_status()
        for p in resp.json().get("items", []):
            if p.get("name") == CORTEX_PROJECT_NAME and p.get("archived_at") is None:
                proj_id = str(p["id"])
                console.print(
                    f"[dim]  Project '{CORTEX_PROJECT_NAME}' already exists — id {proj_id}[/dim]"
                )
                return proj_id

        console.print(f"[cyan]→ Creating project '{CORTEX_PROJECT_NAME}'...[/cyan]")
        payload = {
            "name": CORTEX_PROJECT_NAME,
            "description": "Cortex multi-agent pipeline",
            "working_directory": workspace_path,
            "pipelines": {CORTEX_PIPELINE_KIND: pipeline_id},
            "env_vars": _collect_env_vars(),
        }
        resp = client.post("/projects", json=payload)
        if resp.status_code not in (200, 201):
            console.print(f"[red]✗ Failed to create project: {resp.status_code}[/red]")
            console.print(f"[dim]{resp.text}[/dim]")
            raise SystemExit(1)
        proj_id = str(resp.json()["id"])
        console.print(f"[green]✓ Project created — id {proj_id}[/green]")
        return proj_id


# ---------------------------------------------------------------------------
# Run creation
# ---------------------------------------------------------------------------


def create_run(
    engine_url: str,
    project_id: str,
    pipeline_id: str,
    issue_url: str,
    repo: str,
    issue_number: int,
    workspace_path: str,
) -> str:
    """POST /projects/{project_id}/run/full — returns run_id."""
    initial_state: dict[str, Any] = {
        "extensions": {
            "issue_url": issue_url,
            "issue_number": issue_number,
            "repo": repo,
            "workspace_path": workspace_path,
        }
    }
    with _client(engine_url) as client:
        resp = client.post(
            f"/projects/{project_id}/run/{CORTEX_PIPELINE_KIND}",
            json={"initial_state": initial_state},
        )
        if resp.status_code not in (200, 201):
            console.print(f"[red]✗ Failed to create run: {resp.status_code}[/red]")
            console.print(f"[dim]{resp.text}[/dim]")
            raise SystemExit(1)
        run_id = str(resp.json()["id"])
        return run_id


# ---------------------------------------------------------------------------
# Polling + gate interaction
# ---------------------------------------------------------------------------


def _get_run(engine_url: str, run_id: str) -> dict[str, Any]:
    with _client(engine_url) as client:
        resp = client.get(f"/runs/{run_id}")
        resp.raise_for_status()
        return resp.json()


def _get_run_state(engine_url: str, run_id: str) -> dict[str, Any]:
    with _client(engine_url) as client:
        resp = client.get(f"/runs/{run_id}/state")
        resp.raise_for_status()
        return resp.json()


def _approve_gate(engine_url: str, run_id: str, node_id: str) -> None:
    with _client(engine_url) as client:
        resp = client.post(f"/runs/{run_id}/nodes/{node_id}/approve")
        resp.raise_for_status()


def _reject_gate(engine_url: str, run_id: str, node_id: str, reason: str) -> None:
    """Abort the run (no direct reject endpoint; operator feedback goes in issue)."""
    with _client(engine_url) as client:
        # DAP has /abort but not /reject-with-feedback at gate level.
        # Abort the run and surface the reason to the operator.
        resp = client.post(f"/runs/{run_id}/abort")
        if resp.status_code not in (200, 409):
            resp.raise_for_status()
    console.print(f"[yellow]  Feedback: {reason}[/yellow]")
    console.print("[dim]  Run aborted. Comment on the GitHub issue with the rejection reason.[/dim]")


def _find_pending_gate(engine_url: str, run_id: str) -> str | None:
    """Return the node_id of the pending gate from run state, or None."""
    try:
        state = _get_run_state(engine_url, run_id)
    except httpx.HTTPError:
        return None
    # The gate node id is in state.next or state.extensions.pending_gate
    extensions = state.get("extensions") or {}
    pending = extensions.get("pending_gate") or extensions.get("pending_approval")
    if pending:
        return str(pending)
    # Fall back to known gate node names from the Cortex full bundle
    return None


def _prompt_gate_approval(gate_node: str, no_interactive: bool) -> tuple[bool, str]:
    """Return (approved, reason). In non-interactive mode always approves."""
    if no_interactive:
        return True, ""
    console.print(f"\n[yellow]⏸  Paused at gate:[/yellow] [bold]{gate_node}[/bold]")
    answer = console.input("   Approve? [y/N]: ").strip().lower()
    if answer == "y":
        return True, ""
    reason = console.input("   Rejection reason (optional): ").strip()
    return False, reason


def _known_gate_for_node(node_id: str) -> str:
    """Map a status hint to the canonical gate node id."""
    gate_map = {
        "gate-phase1": "gate-phase1",
        "gate-phase2": "gate-phase2",
        "gate-phase3": "gate-phase3",
    }
    return gate_map.get(node_id, node_id)


def poll_and_handle(
    engine_url: str,
    run_id: str,
    no_interactive: bool,
    watch_only: bool,
) -> None:
    """Poll run status and handle gates until completion or failure."""
    last_status: str | None = None
    start_time = time.monotonic()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task("Running pipeline...", total=None)

        while True:
            time.sleep(POLL_INTERVAL_SECONDS)

            try:
                run = _get_run(engine_url, run_id)
            except httpx.HTTPError as exc:
                console.print(f"[red]✗ Could not fetch run status: {exc}[/red]")
                raise SystemExit(1) from exc

            status = run.get("final_status", "running")

            if status != last_status:
                last_status = status
                style = _STATUS_STYLE.get(status, "white")
                progress.update(
                    task, description=f"[{style}]{status.upper()}[/{style}] — run {run_id[:8]}"
                )

            if status in ("success", "failed", "aborted"):
                break

            if status == "paused":
                progress.stop()

                # Find the pending gate node
                gate_node = _find_pending_gate(engine_url, run_id)
                if not gate_node:
                    # Try to infer from run metadata
                    gate_node = run.get("current_node") or "gate-phase1"

                gate_node = _known_gate_for_node(gate_node)

                if watch_only:
                    console.print(
                        f"\n[yellow]⏸  Paused at:[/yellow] [bold]{gate_node}[/bold]  "
                        f"(--watch mode — not approving)"
                    )
                    console.print(
                        f"  Approve with: dap project run cortex --run-id {run_id} approve"
                    )
                    return

                approved, reason = _prompt_gate_approval(gate_node, no_interactive)

                if approved:
                    console.print(f"[green]✓ Approving gate {gate_node}...[/green]")
                    try:
                        _approve_gate(engine_url, run_id, gate_node)
                    except httpx.HTTPError as exc:
                        console.print(f"[red]✗ Approve failed: {exc}[/red]")
                        raise SystemExit(1) from exc
                else:
                    _reject_gate(engine_url, run_id, gate_node, reason)
                    return

                # Resume polling
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    transient=True,
                    console=console,
                ) as progress2:
                    task2 = progress2.add_task("Resuming...", total=None)
                    # Brief wait for the engine to pick up the approval
                    time.sleep(3)
                    while True:
                        time.sleep(POLL_INTERVAL_SECONDS)
                        try:
                            run = _get_run(engine_url, run_id)
                        except httpx.HTTPError as exc:
                            console.print(f"[red]✗ Could not fetch run status: {exc}[/red]")
                            raise SystemExit(1) from exc
                        status = run.get("final_status", "running")
                        style = _STATUS_STYLE.get(status, "white")
                        progress2.update(
                            task2,
                            description=f"[{style}]{status.upper()}[/{style}] — run {run_id[:8]}",
                        )
                        if status in ("success", "failed", "aborted"):
                            last_status = status
                            break
                        if status == "paused":
                            last_status = status
                            break
                    if status == "paused":
                        continue  # outer while — handle next gate

    # Final report
    elapsed = int(time.monotonic() - start_time)
    style = _STATUS_STYLE.get(last_status or "failed", "white")
    icon = "✅" if last_status == "success" else "❌"
    console.print(
        f"\n{icon} [{style}]Pipeline {last_status or 'done'}[/{style}]  "
        f"run {run_id[:8]}  duration {elapsed}s"
    )
    if last_status not in ("success",):
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Default workspace path
# ---------------------------------------------------------------------------


def default_workspace_path(repo: str) -> str:
    """Return the conventional Cortex workspace path for a repo."""
    repo_slug = repo.replace("/", "_")
    return str(os.path.expanduser(f"~/.cortex/projects/{repo_slug}/repo"))


# ---------------------------------------------------------------------------
# High-level command functions
# ---------------------------------------------------------------------------


def cortex_run(
    issue_url: str,
    engine_url: str,
    no_interactive: bool,
    watch: bool,
    workspace: str | None,
) -> None:
    """Implement `dap project run cortex <issue-url>`."""
    # Parse URL
    try:
        repo, issue_number = parse_issue_url(issue_url)
    except ValueError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise SystemExit(1) from exc

    console.print(f"[bold]Cortex pipeline[/bold] — {repo}#{issue_number}")
    console.print(f"[dim]  Engine: {engine_url}[/dim]")

    # Check engine
    check_engine(engine_url)

    # Load bundle
    try:
        bundle = load_cortex_bundle()
    except ImportError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise SystemExit(1) from exc

    # Import pipeline
    pipeline_id = ensure_pipeline_imported(engine_url, bundle)

    # Workspace
    ws_path = workspace or default_workspace_path(repo)

    # Create / reuse project
    project_id = ensure_project(engine_url, pipeline_id, ws_path)

    # Create run
    console.print(f"[cyan]→ Starting run for {repo}#{issue_number}...[/cyan]")
    run_id = create_run(
        engine_url=engine_url,
        project_id=project_id,
        pipeline_id=pipeline_id,
        issue_url=issue_url,
        repo=repo,
        issue_number=issue_number,
        workspace_path=ws_path,
    )
    console.print(f"[green]✓ Run created — id {run_id}[/green]")
    console.print(f"[dim]  Poll: GET {engine_url}/runs/{run_id}[/dim]")

    # Monitor
    poll_and_handle(
        engine_url=engine_url,
        run_id=run_id,
        no_interactive=no_interactive,
        watch_only=watch,
    )


def cortex_approve(run_id: str, engine_url: str) -> None:
    """Implement `dap project approve cortex <run-id>`."""
    run = _get_run(engine_url, run_id)
    status = run.get("final_status")
    if status != "paused":
        console.print(f"[yellow]⚠ Run {run_id[:8]} is not paused (status={status})[/yellow]")
        raise SystemExit(1)

    gate_node = _find_pending_gate(engine_url, run_id) or "gate-phase1"
    gate_node = _known_gate_for_node(gate_node)
    console.print(f"[cyan]→ Approving gate {gate_node} for run {run_id[:8]}...[/cyan]")
    try:
        _approve_gate(engine_url, run_id, gate_node)
    except httpx.HTTPError as exc:
        console.print(f"[red]✗ Approve failed: {exc}[/red]")
        raise SystemExit(1) from exc
    console.print(f"[green]✓ Approved — run {run_id[:8]} resuming[/green]")


def cortex_reject(run_id: str, reason: str, engine_url: str) -> None:
    """Implement `dap project reject cortex <run-id> [reason]`."""
    run = _get_run(engine_url, run_id)
    status = run.get("final_status")
    if status != "paused":
        console.print(f"[yellow]⚠ Run {run_id[:8]} is not paused (status={status})[/yellow]")
        raise SystemExit(1)

    gate_node = _find_pending_gate(engine_url, run_id) or "gate-phase1"
    gate_node = _known_gate_for_node(gate_node)
    console.print(f"[cyan]→ Rejecting gate {gate_node} for run {run_id[:8]}...[/cyan]")
    _reject_gate(engine_url, run_id, gate_node, reason)
    console.print(f"[green]✓ Run {run_id[:8]} aborted[/green]")


def cortex_state(run_id: str, engine_url: str) -> None:
    """Implement `dap project state cortex <run-id>`."""
    run = _get_run(engine_url, run_id)
    status = run.get("final_status", "unknown")
    style = _STATUS_STYLE.get(status, "white")
    console.print(f"Run [bold]{run_id}[/bold]")
    console.print(f"  Status:   [{style}]{status}[/{style}]")
    console.print(f"  Pipeline: {run.get('pipeline_id', '?')} v{run.get('pipeline_version', '?')}")
    console.print(f"  Project:  {run.get('project_id', 'ad-hoc')}")
    started = run.get("started_at", "?")
    ended = run.get("ended_at", "—")
    console.print(f"  Started:  {started}")
    console.print(f"  Ended:    {ended}")
