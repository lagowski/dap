"""Cortex pipeline operations — implementation behind `dap project run/approve/reject/state cortex`.

Calls the DAP engine REST API. Does NOT import Cortex runtime code (nodes,
adapters, pipeline classes) from ``cortex.*`` — the cortex package lives in
the external ``Dixter999/cortex-project`` repo (extracted from this monorepo
in c3730a4) and ships to PyPI as ``dap-cortex``. The only ``cortex.*``
touchpoint here is ``importlib.resources.files("cortex.dap_bundles")`` in
``load_cortex_bundle()`` — a static-data lookup against the installed
package namespace, no behavioural import. Distribution name is
``dap-cortex``; the import namespace is ``cortex``.
"""

from __future__ import annotations

import importlib.resources
import json
import os
import re
import subprocess
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.live import Live
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

# Per-node-status glyph + rich style for the live progress checklist (#662).
# NodeStatus is Literal["pending","running","success","failed","skipped"].
_NODE_GLYPH: dict[str, tuple[str, str]] = {
    "success": ("✓", "green"),
    "running": ("▶", "cyan"),
    "pending": ("·", "dim"),
    "failed": ("✗", "red"),
    "skipped": ("↷", "yellow"),
}
# Glyph used for any status not in _NODE_GLYPH — resilient to engine changes.
_NODE_GLYPH_UNKNOWN: tuple[str, str] = ("•", "white")


def _format_elapsed(elapsed_s: float) -> str:
    """Format seconds as ``Hh Mm Ss`` (hours dropped when zero)."""
    total = int(elapsed_s)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    return f"{minutes}m {seconds}s"


def _format_progress(run: dict[str, Any], elapsed_s: float) -> str:
    """Render a live per-node progress view as rich-console markup (#662 Phase 1).

    Builds a header line (current node, or the run's ``final_status`` when no
    node is active) plus a per-node checklist with status glyphs, in the
    insertion order of ``node_statuses`` (≈ execution order). Resilient to a
    missing/empty ``node_statuses`` and unknown status values.
    """
    current_node = run.get("current_node")
    final_status = run.get("final_status", "running")
    elapsed = _format_elapsed(elapsed_s)

    if current_node:
        header = f"[bold cyan]▶ {current_node}[/bold cyan]  [dim]({elapsed})[/dim]"
    else:
        style = _STATUS_STYLE.get(final_status, "white")
        header = f"[{style}]{final_status.upper()}[/{style}]  [dim]({elapsed})[/dim]"

    lines = [header]
    node_statuses = run.get("node_statuses") or {}
    for node_id, status in node_statuses.items():
        glyph, style = _NODE_GLYPH.get(status, _NODE_GLYPH_UNKNOWN)
        lines.append(f"  [{style}]{glyph}[/{style}] {node_id}")
    return "\n".join(lines)


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
    """Load cortex-full.pipeline-bundle.json from the installed ``dap-cortex`` package.

    Requires the ``dap-cortex`` PyPI distribution (extracted from this
    monorepo to ``Dixter999/cortex-project``) to be installed in the
    current Python environment. Note: distribution name is ``dap-cortex``
    but the import namespace is ``cortex`` — the bundle is looked up via
    ``importlib.resources.files("cortex.dap_bundles")``. Raises
    ImportError with a clear install instruction if not.
    """
    try:
        bundle_ref = importlib.resources.files("cortex.dap_bundles") / CORTEX_BUNDLE_NAME
        bundle_text = bundle_ref.read_text(encoding="utf-8")
    except (ModuleNotFoundError, FileNotFoundError) as exc:
        raise ImportError(
            "Could not load the Cortex pipeline bundle.\n\n"
            "The `dap-cortex` package must be installed in this environment.\n"
            "Run: pip install dap-cortex\n"
            "Or:  uv add dap-cortex\n\n"
            "Source: https://github.com/Dixter999/cortex-project"
        ) from exc
    return dict(json.loads(bundle_text))


# ---------------------------------------------------------------------------
# Engine health check
# ---------------------------------------------------------------------------


def _resolve_auth_token(token: str | None) -> str:
    """Resolve the engine bearer token: explicit arg wins, else ``DAP_AUTH_TOKEN``.

    Returns ``""`` when neither is set (or only whitespace) — the engine's
    ``/health`` is public, but every other endpoint 401s, so a missing token
    surfaces as a clear 401 the caller already handles.
    """
    return (token or os.environ.get("DAP_AUTH_TOKEN") or "").strip()


def _client(engine_url: str, token: str | None = None) -> httpx.Client:
    """HTTP client for the engine, carrying ``Authorization: Bearer`` when a
    token is available. The engine accepts a JWT or an opaque ``dap_*`` API
    token (``/auth/api-tokens``); the CLI forwards whatever is configured via
    ``--token`` / ``DAP_AUTH_TOKEN``."""
    resolved = _resolve_auth_token(token)
    headers = {"Authorization": f"Bearer {resolved}"} if resolved else {}
    return httpx.Client(base_url=engine_url.rstrip("/"), timeout=10.0, headers=headers)


def _export_token_to_env(token: str | None) -> None:
    """Propagate an explicit ``--token`` into ``DAP_AUTH_TOKEN``.

    The many internal helpers build their own ``_client(engine_url)`` and
    resolve the token from the env, so a token passed only as a flag is
    exported here once at the entrypoint. No-op for empty/whitespace tokens
    (and a no-op overwrite when the value already came from the env var).
    """
    t = (token or "").strip()
    if t:
        os.environ["DAP_AUTH_TOKEN"] = t


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
                # Keep working_directory and env_vars current on every run so
                # bash nodes (git-branch) always get the right cwd. Without
                # this, a project created before working_directory was set, or
                # one pointing to the wrong path, would silently pass the wrong
                # cwd to every bash node for the lifetime of the project (#224).
                #
                # Build from existing project data to preserve fields we don't
                # control (repo_url, default_branch) and merge pipeline bindings
                # rather than replacing them wholesale (Copilot review).
                update_payload = {
                    "name": p.get("name", CORTEX_PROJECT_NAME),
                    "description": p.get("description", "Cortex multi-agent pipeline"),
                    "working_directory": workspace_path,
                    "repo_url": p.get("repo_url"),
                    "default_branch": p.get("default_branch", "main"),
                    "pipelines": {
                        **p.get("pipelines", {}),
                        CORTEX_PIPELINE_KIND: pipeline_id,
                    },
                    "env_vars": _collect_env_vars(),
                }
                resp_put = client.put(f"/projects/{proj_id}", json=update_payload)
                resp_put.raise_for_status()
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
        return dict(resp.json())


def _get_run_state(engine_url: str, run_id: str) -> dict[str, Any]:
    with _client(engine_url) as client:
        resp = client.get(f"/runs/{run_id}/state")
        resp.raise_for_status()
        return dict(resp.json())


def _approve_gate(engine_url: str, run_id: str, node_id: str) -> None:
    with _client(engine_url) as client:
        resp = client.post(f"/runs/{run_id}/nodes/{node_id}/approve")
        resp.raise_for_status()
    console.print(
        f"[green]✓ Approval registered for {run_id[:8]} — run resuming in background[/green]"
    )


def _reject_gate(engine_url: str, run_id: str, node_id: str, reason: str) -> None:
    """Abort the run (no direct reject endpoint; operator feedback goes in issue)."""
    with _client(engine_url) as client:
        # DAP has /abort but not /reject-with-feedback at gate level.
        # Abort the run and surface the reason to the operator.
        resp = client.post(f"/runs/{run_id}/abort")
        if resp.status_code not in (200, 409):
            resp.raise_for_status()
    console.print(f"[yellow]  Feedback: {reason}[/yellow]")
    console.print(
        "[dim]  Run aborted. Comment on the GitHub issue with the rejection reason.[/dim]"
    )


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


def _poll_until_settled(
    engine_url: str,
    run_id: str,
    label: str,
    show_progress: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Poll GET /runs/{run_id} until status is terminal or paused.

    Returns (final_status, last_run_dict). When ``show_progress`` is True a
    live per-node progress view (header + per-node checklist + elapsed) is
    rendered from ``current_node``/``node_statuses`` (#662 Phase 1); otherwise
    a bare run-level spinner is shown.
    """
    if not show_progress:
        return _poll_until_settled_spinner(engine_url, run_id, label)

    start = time.monotonic()
    last_status = "running"
    last_run: dict[str, Any] = {}
    with Live(label, console=console, transient=True, refresh_per_second=4) as live:
        while True:
            time.sleep(POLL_INTERVAL_SECONDS)
            try:
                last_run = _get_run(engine_url, run_id)
            except httpx.HTTPError as exc:
                console.print(f"[red]✗ Could not fetch run status: {exc}[/red]")
                raise SystemExit(1) from exc
            last_status = last_run.get("final_status", "running")
            live.update(_format_progress(last_run, time.monotonic() - start))
            if last_status in ("success", "failed", "aborted", "paused"):
                break
    return last_status, last_run


def _poll_until_settled_spinner(
    engine_url: str,
    run_id: str,
    label: str,
) -> tuple[str, dict[str, Any]]:
    """Bare run-level spinner poll loop (the ``--no-progress`` path)."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task(label, total=None)
        last_status = "running"
        last_run: dict[str, Any] = {}
        while True:
            time.sleep(POLL_INTERVAL_SECONDS)
            try:
                last_run = _get_run(engine_url, run_id)
            except httpx.HTTPError as exc:
                console.print(f"[red]✗ Could not fetch run status: {exc}[/red]")
                raise SystemExit(1) from exc
            last_status = last_run.get("final_status", "running")
            style = _STATUS_STYLE.get(last_status, "white")
            progress.update(
                task,
                description=f"[{style}]{last_status.upper()}[/{style}] — run {run_id[:8]}",
            )
            if last_status in ("success", "failed", "aborted", "paused"):
                break
    return last_status, last_run


def _handle_gate(
    engine_url: str,
    run_id: str,
    run: dict[str, Any],
    watch_only: bool,
    no_interactive: bool,
) -> bool:
    """Handle a paused gate. Returns True to continue polling, False to stop."""
    gate_node = _find_pending_gate(engine_url, run_id)
    if not gate_node:
        gate_node = run.get("current_node") or "gate-phase1"
    gate_node = _known_gate_for_node(gate_node)

    if watch_only:
        console.print(
            f"\n[yellow]⏸  Paused at:[/yellow] [bold]{gate_node}[/bold]  "
            f"(--watch mode — not approving)"
        )
        console.print(f"  Approve with: dap project run cortex --run-id {run_id} approve")
        return False

    approved, reason = _prompt_gate_approval(gate_node, no_interactive)
    if not approved:
        _reject_gate(engine_url, run_id, gate_node, reason)
        return False

    console.print(f"[green]✓ Approving gate {gate_node}...[/green]")
    try:
        _approve_gate(engine_url, run_id, gate_node)
    except httpx.HTTPError as exc:
        console.print(f"[red]✗ Approve failed: {exc}[/red]")
        raise SystemExit(1) from exc
    return True


def poll_and_handle(
    engine_url: str,
    run_id: str,
    no_interactive: bool,
    watch_only: bool,
    show_progress: bool = True,
) -> None:
    """Poll run status and handle gates until completion or failure."""
    start_time = time.monotonic()
    last_status = "running"

    while True:
        last_status, last_run = _poll_until_settled(
            engine_url, run_id, "Running pipeline...", show_progress=show_progress
        )
        if last_status != "paused":
            break
        should_continue = _handle_gate(engine_url, run_id, last_run, watch_only, no_interactive)
        if not should_continue:
            return
        time.sleep(3)

    # Final report
    elapsed = int(time.monotonic() - start_time)
    style = _STATUS_STYLE.get(last_status, "white")
    icon = "✅" if last_status == "success" else "❌"
    console.print(
        f"\n{icon} [{style}]Pipeline {last_status or 'done'}[/{style}]  "
        f"run {run_id[:8]}  duration {elapsed}s"
    )
    if last_status != "success":
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Default workspace path
# ---------------------------------------------------------------------------


def default_workspace_path(repo: str) -> str:
    """Return the conventional Cortex workspace path for a repo.

    Uses the same ``owner-name`` slug that ``cortex/init/profile.py:_repo_slug``
    produces — dash separator, not underscore (#224).
    """
    repo_slug = repo.replace("/", "-")
    return str(os.path.expanduser(f"~/.cortex/projects/{repo_slug}/repo"))


# ---------------------------------------------------------------------------
# Workspace sync
# ---------------------------------------------------------------------------


def _sync_workspace(ws_path: str) -> None:
    """Sync the workspace clone to its default branch before starting a run.

    Mirrors cortex-project cli.py:_sync_workspace. Non-fatal: logs a warning
    and continues if the workspace doesn't exist or git fails — execution.py
    _sync_base_branch is a per-agent fallback inside the pipeline (#245).

    """
    # Expand ~ so --workspace ~/... works correctly.
    workspace = Path(ws_path).expanduser()
    cwd = str(workspace)
    if not workspace.exists():
        console.print(f"[yellow]⚠  Workspace not found at {workspace} — skipping sync[/yellow]")
        return
    try:
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=cwd,
            check=True,
            capture_output=True,
            timeout=60,
        )
        result = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        branch = result.stdout.strip().split("/")[-1] if result.returncode == 0 else "main"
        subprocess.run(
            ["git", "checkout", branch],
            cwd=cwd,
            check=True,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "reset", "--hard", f"origin/{branch}"],
            cwd=cwd,
            check=True,
            capture_output=True,
            timeout=30,
        )
        console.print(f"[dim]  Workspace synced → origin/{branch}[/dim]")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        stderr = b""
        if isinstance(e, subprocess.CalledProcessError):
            stderr = e.stderr or b""
        stderr_str = stderr.decode("utf-8", errors="replace")[:200]
        detail = f" — {stderr_str}" if stderr_str else ""
        console.print(f"[yellow]⚠  Workspace sync failed: {e}{detail}[/yellow]")


# ---------------------------------------------------------------------------
# High-level command functions
# ---------------------------------------------------------------------------


def cortex_run(
    issue_url: str,
    engine_url: str,
    no_interactive: bool,
    watch: bool,
    workspace: str | None,
    token: str | None = None,
    show_progress: bool = True,
) -> None:
    """Implement `dap project run cortex <issue-url>`."""
    _export_token_to_env(token)
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

    # Sync workspace to default branch HEAD before the run so Phase 1 agents
    # read current code and the coder branches from the right baseline (#241).
    _sync_workspace(ws_path)

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
        show_progress=show_progress,
    )


def cortex_approve(run_id: str, engine_url: str, token: str | None = None) -> None:
    """Implement `dap project approve cortex <run-id>`."""
    _export_token_to_env(token)
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


def cortex_reject(run_id: str, reason: str, engine_url: str, token: str | None = None) -> None:
    """Implement `dap project reject cortex <run-id> [reason]`."""
    _export_token_to_env(token)
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


def _state_to_dict(run: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Build a JSON-serialisable dict from run + run-state data."""
    extensions = state.get("extensions") or {}
    decisions_raw = extensions.get("decisions") or []
    return {
        "run_id": run.get("id", ""),
        "status": run.get("final_status", "unknown"),
        "issue_title": extensions.get("issue_title", ""),
        "current_phase": extensions.get("current_phase", ""),
        "pipeline_id": run.get("pipeline_id", ""),
        "pipeline_version": run.get("pipeline_version", ""),
        "project_id": run.get("project_id", ""),
        "started_at": run.get("started_at"),
        "ended_at": run.get("ended_at"),
        "next_nodes": extensions.get("next_nodes") or [],
        "task_assignments": extensions.get("task_assignments") or {},
        "decisions": decisions_raw[-5:],
    }


def _json_default(obj: object) -> str:
    """Fallback serialiser for json.dumps — handles datetime & Decimal."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def cortex_state(
    run_id: str, engine_url: str, fmt: str = "table", token: str | None = None
) -> None:
    """Implement `dap project state cortex <run-id>`."""
    _export_token_to_env(token)
    try:
        run = _get_run(engine_url, run_id)
    except httpx.HTTPStatusError as exc:
        # Only treat 404 as "not found" — other status codes are real errors
        # that should surface rather than be swallowed as "not_found".
        if exc.response.status_code == 404 and fmt == "json":  # noqa: PLR2004
            print(json.dumps({"error": "not_found", "run_id": run_id}))
            return
        raise
    except (httpx.HTTPError, SystemExit):
        raise

    if fmt == "json":
        try:
            state = _get_run_state(engine_url, run_id)
        except httpx.HTTPError:
            state = {}
        result = _state_to_dict(run, state)
        print(json.dumps(result, indent=2, default=_json_default))
        return

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
