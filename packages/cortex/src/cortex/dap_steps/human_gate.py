"""Human gate — pauses the DAP run for operator approval.

Called as a ``python-func`` node at phase-boundary checkpoints
(phase1_gate, phase2_gate, phase3_gate in the Cortex bundle).

Pause mechanism
---------------
1. This function calls ``POST /runs/{run_id}/pause`` on the DAP engine.
2. The engine's run_registry marks the run as paused, then cancels the
   background asyncio Task via ``task.cancel()``.
3. The cancellation propagates to the ``await client.post(...)`` call here
   and bubbles up through the python-func adapter (which re-raises
   ``CancelledError``) and through LangGraph to the background task handler.
4. The handler sees ``was_paused(run_id) == True`` and calls
   ``repo.pause_run()`` — run stays paused with its LangGraph checkpoint
   preserved.
5. Operator reviews GitHub issue / state and calls
   ``POST /runs/{run_id}/resume`` to continue.

Resolving run_id
----------------
Priority order:
1. ``state["__dap_run_id"]`` — injected when node_executor adds it to the
   python-func state dict (DAP #<future> — not yet merged).
2. ``config["__dap_run_id"]`` — set as a runtime_config override on the node.
3. Regex scan of ``state["prompt_xml"]`` for ``<run_id>…</run_id>`` —
   works today if the gate agent's prompt template includes ``{{ run_id }}``.

Engine URL
----------
Resolved from ``config["dap_engine_url"]`` → ``DAP_ENGINE_URL`` env var →
``http://{DAP_ENGINE_HOST}:{DAP_ENGINE_PORT}`` (defaults 127.0.0.1:7333).
"""

from __future__ import annotations

import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)

__all__ = ["noop", "run"]

_RUN_ID_RE = re.compile(r"<run_id>\s*(.*?)\s*</run_id>", re.DOTALL)


async def run(state: dict, config: dict) -> dict:
    """Pause the DAP run, blocking until operator resumes it.

    Designed to be wired as a python-func node in the Cortex pipeline bundle.
    Returns an empty dict on the (unreachable) happy path; in practice the
    DAP engine cancels the task before this function returns.
    """
    run_id = _resolve_run_id(state, config)
    if not run_id:
        logger.error(
            "human_gate: could not resolve run_id from state/config/prompt_xml "
            "— skipping pause (pipeline will continue without approval gate)"
        )
        return {"human_gate_skipped": True}

    engine_url = _resolve_engine_url(config)
    logger.info("human_gate: requesting pause for run %s at %s", run_id, engine_url)

    async with httpx.AsyncClient(timeout=15.0) as client:
        # This call triggers task.cancel() in the engine's RunRegistry.
        # asyncio.CancelledError is delivered to this await and propagates
        # up through the python-func adapter — the line below never completes
        # normally when the engine is live.
        response = await client.post(f"{engine_url}/runs/{run_id}/pause")
        response.raise_for_status()

    # Unreachable during normal operation — kept for type-correctness.
    return {}  # pragma: no cover


def _resolve_run_id(state: dict, config: dict) -> str:
    """Return the DAP run_id from the first available source."""
    if run_id := state.get("__dap_run_id"):
        return str(run_id)
    if run_id := config.get("__dap_run_id"):
        return str(run_id)
    # Fallback: parse from the Jinja-rendered prompt XML.
    if m := _RUN_ID_RE.search(state.get("prompt_xml", "")):
        return m.group(1)
    return ""


async def noop(state: dict, config: dict) -> dict:
    """Pass-through gate node for use with interrupt_before / approval_required_nodes.

    When the pipeline uses ``approval_required_nodes: ["gate-phase1"]``, the
    DAP engine compiles the graph with ``interrupt_before=["gate-phase1"]``.
    LangGraph pauses BEFORE this node. On operator approval (POST /resume or
    POST /nodes/{id}/approve), LangGraph skips the interrupt and executes this
    node — which just returns an empty dict so execution continues (#164).
    """
    return {}


def _resolve_engine_url(config: dict) -> str:
    """Return the DAP engine base URL without a trailing slash."""
    if url := config.get("dap_engine_url"):
        return str(url).rstrip("/")
    if url := os.environ.get("DAP_ENGINE_URL"):
        return url.rstrip("/")
    host = os.environ.get("DAP_ENGINE_HOST", "127.0.0.1")
    port = os.environ.get("DAP_ENGINE_PORT", "7333")
    return f"http://{host}:{port}"
