"""Interaction-log recorder for node executions (#785).

Follow-up to the assistant-path recorder in :mod:`dap_engine.api.assistant`:
same mechanism (:func:`dap_engine.persistence.interaction_log.record_interaction`
+ :func:`dap_engine.redaction.redact`), extended here so the compliance
surface also covers **pipeline node model calls**.

Design:

- Only records for **model-invoking** runtimes (``adapter.kind`` in
  :data:`_MODEL_INVOKING_KINDS`). ``shell`` (bash, python-func) and
  generic ``http`` never call an LLM and are skipped — the exec log
  already covers those.
- Redacts **before** it truncates. Redacting after truncation could
  mid-string a token and defeat the pattern-based rules. Truncation
  caps each side at :data:`DEFAULT_MAX_BYTES_PER_SIDE` bytes (defaults
  to 64 KB); when a side is truncated, ``extra`` carries the original
  byte count so audit users can see how much was dropped.
- Config flag is read from ``DAP_INTERACTION_LOG_ENABLED`` (mirrors the
  assistant-path :class:`~dap_engine.config.InteractionLogConfig`
  default of ``True``). Env-var-read keeps this module callable from
  the node executor without threading a live :class:`EngineConfig`
  through :class:`~dap_engine.execution.runner.PipelineRunner`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from dap_types import RuntimeResult
from sqlalchemy.orm import Session

from dap_engine.persistence.interaction_log import record_interaction
from dap_engine.redaction import redact

__all__ = [
    "DEFAULT_MAX_BYTES_PER_SIDE",
    "is_enabled",
    "record_node_interaction",
]

# 64 KB per side keeps typical prompts (few KB) and typical responses
# (up to ~30 KB for verbose cortex output) intact while capping the
# per-row storage cost so a runaway response can't fill the log table.
DEFAULT_MAX_BYTES_PER_SIDE = 65536

# Runtime ``kind`` values that indicate an actual LLM call. ``api``
# covers the direct-SDK ``api-call`` adapter; ``cli`` covers the
# subprocess wrappers (``claude-code``, ``codex``, ``aider``,
# ``gemini-cli``) — see ``packages/runtimes/src/dap_runtimes/adapters/``.
_MODEL_INVOKING_KINDS = frozenset({"api", "cli"})


def is_enabled() -> bool:
    """``True`` unless ``DAP_INTERACTION_LOG_ENABLED`` is set to a
    false-like value (``0``, ``false``, ``no``, or the empty string).

    Mirrors :class:`~dap_engine.config.InteractionLogConfig` — enabled
    by default, opt-out via env var.
    """
    raw = os.environ.get("DAP_INTERACTION_LOG_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", ""}


def record_node_interaction(
    session: Session,
    *,
    run_id: str,
    node_id: str,
    execution_id: str,
    runtime_id: str,
    adapter_kind: str,
    prompt_xml: str,
    result: RuntimeResult,
    known_secrets: Mapping[str, str],
    provider: str | None = None,
    model: str | None = None,
    max_bytes_per_side: int = DEFAULT_MAX_BYTES_PER_SIDE,
) -> None:
    """Append one redacted interaction row for a model-invoking node.

    No-op when the log is disabled (``DAP_INTERACTION_LOG_ENABLED=0``)
    or when the adapter is not model-invoking (``shell``/``http``).

    Caller owns the transaction — this only calls ``session.add`` via
    :func:`record_interaction`, matching the assistant path's contract.
    """
    if not is_enabled():
        return
    if adapter_kind not in _MODEL_INVOKING_KINDS:
        return

    req_text, req_original_bytes = _redact_and_truncate(
        prompt_xml, known_secrets, max_bytes_per_side
    )
    resp_text, resp_original_bytes = _redact_and_truncate(
        result.output, known_secrets, max_bytes_per_side
    )

    extra: dict[str, Any] = {
        "run_id": run_id,
        "node_id": node_id,
        "execution_id": execution_id,
        "runtime_id": runtime_id,
    }
    if req_original_bytes is not None:
        extra["request_truncated_from_bytes"] = req_original_bytes
    if resp_original_bytes is not None:
        extra["response_truncated_from_bytes"] = resp_original_bytes

    record_interaction(
        session,
        user_id=None,
        surface="node",
        provider=provider,
        model=model,
        redacted_request=[{"role": "user", "content": req_text}],
        redacted_response=resp_text,
        tokens_used=result.tokens_used,
        extra=extra,
    )


def _redact_and_truncate(
    text: str | None,
    known_secrets: Mapping[str, str],
    max_bytes: int,
) -> tuple[str, int | None]:
    """Redact secrets from ``text`` and truncate the redacted result to
    ``max_bytes`` bytes on a UTF-8 codepoint boundary.

    Returns ``(text_out, original_bytes)`` — ``original_bytes`` is
    ``None`` when the redacted text already fit under ``max_bytes``.
    """
    if not text:
        return "", None
    redacted = redact(text, known_secrets=known_secrets)
    encoded = redacted.encode("utf-8")
    if len(encoded) <= max_bytes:
        return redacted, None
    head = _safe_utf8_head(encoded, max_bytes)
    return (
        f"{head}\n[TRUNCATED — original was {len(encoded)} bytes]",
        len(encoded),
    )


def _safe_utf8_head(data: bytes, max_bytes: int) -> str:
    """Largest UTF-8-decodable prefix of ``data`` up to ``max_bytes``.

    A single UTF-8 codepoint is at most 4 bytes, so at most 3 trailing
    bytes need to be peeled off to land on a codepoint boundary.
    """
    head = data[:max_bytes]
    for _ in range(4):
        try:
            return head.decode("utf-8")
        except UnicodeDecodeError:
            head = head[:-1]
    return ""
