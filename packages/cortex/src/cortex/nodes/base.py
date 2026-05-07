"""Shared base pattern for Phase 1 enrichment nodes.

Every enrichment agent follows the same flow:
  read issue → call LLM → update section → post comment → log to audit

Usage in a concrete node:

    def mockup(state: CortexState) -> dict:
        return enrichment_step(state, "mockup", "Description")
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from cortex.backends.base import LLMRequest
from cortex.backends.registry import create_backend_with_fallback as create_backend
from cortex.config.settings import get_agent_backend_config, load_settings
from cortex.init.profile import repo_clone_path
from cortex.nodes.validators import validate_response
from cortex.persistence.audit import log_decision, log_issue_update
from cortex.templates import get_issue_section, parse_issue_sections, update_issue_section
from cortex.tools.github import create_issue_comment, read_issue, update_issue_body
from cortex.workspace import (
    AGENT_NAMES,
    agent_memory_dir,
    compose_claude_md,
    init_workspace,
)

logger = logging.getLogger(__name__)


def _scope_issue_context(body: str, context_sections: object) -> str:
    """Filter the issue body to only the sections this agent should see.

    Args:
        body: Full markdown issue body.
        context_sections: From agents.yaml. One of:
            - "all" or None — include the full body unchanged
            - [] (empty list) — include nothing (first agent / raw input only)
            - ["Description", "Technical Specification"] — include only those

    Returns:
        Markdown-formatted slice of the issue body.
    """
    if context_sections == "all" or context_sections is None:
        return body
    if not isinstance(context_sections, list):
        return body  # Defensive: unknown shape, fall back to full body

    if not context_sections:
        # ``[]`` means "raw issue only — first agent" (agents.yaml:27).
        # The whole body, no section filtering. Comments/feedback opt-out
        # for first agents is handled separately at the call site (#119).
        return body

    sections = parse_issue_sections(body)
    parts: list[str] = []
    for name in context_sections:
        # Case-insensitive lookup
        for key, content in sections.items():
            if key.lower() == name.lower():
                parts.append(f"## {key}\n{content}")
                break
    return "\n\n".join(parts)


def _format_feedback_block(feedback: str) -> str:
    """Format operator feedback from a previous rejected run (#108).

    Phase 1 enrichment agents see this prepended to their issue context
    when ``state["human_feedback"]`` is set — so a re-run after
    ``cortex reject`` actually consumes the operator's reasoning instead
    of producing the same output a second time. Cleared by ``finalize``
    so it doesn't leak into Phase 2 or later runs.
    """
    return (
        "## Operator feedback from previous run\n"
        "_The previous Phase 1 attempt was rejected with this feedback. "
        "Address it explicitly in your output — do not reproduce the "
        "behavior that was rejected._\n\n"
        f"{feedback}"
    )


def _format_comments_block(comments: list[dict[str, str]]) -> str:
    """Format issue comments as a markdown block for inclusion in LLM prompts.

    Args:
        comments: List of {"author", "body", "created_at"} dicts.

    Returns:
        Formatted markdown string, or empty string if no comments.
    """
    if not comments:
        return ""
    lines = ["## Comments (chronological)",
             "_Comments may clarify or narrow the body's scope. "
             "If they conflict with the body, surface this in your output "
             "and prefer the most recent direction._", ""]
    for c in comments:
        lines.append(f"**{c.get('author', 'unknown')}** ({c.get('created_at', '')}):")
        lines.append(c.get("body", ""))
        lines.append("")
    return "\n".join(lines)


def _apply_workspace_context(
    agent_config: dict,
    agent_name: str,
    repo: str,
    user_prompt: str,
) -> tuple[dict, str]:
    """If a workspace exists for this repo, inject context.

    For claude_cli / opencode_cli: set cwd to the repo workspace and write the
    composed CLAUDE.md to a per-agent compiled file used as the
    `--append-system-prompt-file`.

    For ollama / api: prepend the composed context to the user prompt
    (these backends have no file access).

    Respects ``context_sections: []`` as an opt-out — first agents like
    mockup that should see only the raw issue text get nothing injected.
    Fixes issue #39.

    Returns the (possibly updated) agent_config and user_prompt.
    """
    # Opt-out: explicit empty context_sections means "no workspace context"
    if agent_config.get("context_sections") == []:
        return agent_config, user_prompt

    workspace = repo_clone_path(repo)
    if not workspace.exists() or agent_name not in AGENT_NAMES:
        return agent_config, user_prompt

    # Self-heal a missing or partially-deleted .cortex/ tree (e.g. after
    # `git clean -fd`) so this function never crashes on a re-mintable
    # workspace. Idempotent — see cortex/workspace.py:70 (#182).
    init_workspace(repo)

    composed = compose_claude_md(repo, agent_name)
    if not composed:
        return agent_config, user_prompt

    backend = agent_config.get("backend", "")

    if backend in ("claude_cli", "opencode_cli"):
        # Native: cwd + system-prompt file
        agent_config = dict(agent_config)
        agent_config["cwd"] = str(workspace)
        compiled = agent_memory_dir(repo, agent_name) / "_compiled.md"
        # Belt-and-suspenders: even if init_workspace was skipped above
        # for any reason, ensure the per-agent dir exists before writing.
        compiled.parent.mkdir(parents=True, exist_ok=True)
        compiled.write_text(composed)
        agent_config["instructions_file"] = str(compiled)
    else:
        # ollama / api: inject into prompt
        user_prompt = f"## Project Context\n\n{composed}\n\n---\n\n{user_prompt}"

    return agent_config, user_prompt


def _llm_call(
    state: dict,
    agent_name: str,
    section_name: str,
) -> tuple[str, dict]:
    """Pure LLM call: read issue, build prompt, invoke backend. No external writes.

    Returns ``(section_content, audit_dict)`` where ``audit_dict`` contains:
        ``tokens_used``, ``cost_usd``, ``section``, ``content_after``,
        ``backend``, ``model``, ``input_tokens``, ``output_tokens``, ``duration_ms``.

    Guaranteed zero calls to ``update_issue_body``, ``log_decision``,
    ``log_issue_update``, or ``create_issue_comment`` — safe to call from a DAP
    node that needs idempotent retry semantics.
    """
    # Normalise state shape: when called from DAP's python-func runtime the
    # incoming dict is PipelineState-shaped (Cortex fields in extensions{}).
    # from_pipeline_state() maps it back to CortexState keys transparently;
    # it is a no-op when state is already CortexState-shaped (#286).
    from cortex.adapters.pipeline_state import from_pipeline_state
    state = from_pipeline_state(state)

    settings = load_settings()
    agent_config = get_agent_backend_config(agent_name)
    token = settings.get_github_token(agent_config["github_token_role"])

    issue_data = read_issue.invoke({
        "repo": state["repo"],
        "issue_number": state["issue_number"],
        "token": token,
    })
    current_body = issue_data.get("body", "")
    issue_comments = issue_data.get("comments", [])

    system_prompt = agent_config.get("system_prompt", "")
    # Inject project context.md into system prompt when available (#268).
    # Must also be in _llm_call (not only enrichment_step) because Phase 1
    # nodes call _llm_call directly — the enrichment_step path is not used.
    try:
        from cortex.init.profile import context_path
        _ctx_file = context_path(state["repo"])
        if _ctx_file.is_file():
            _ctx = _ctx_file.read_text(encoding="utf-8", errors="replace")
            if _ctx.strip():
                system_prompt = _ctx.strip() + "\n\n---\n\n" + system_prompt
    except Exception:
        pass
    context_sections = agent_config.get("context_sections", "all")
    scoped_body = _scope_issue_context(current_body, context_sections)

    comments_block = ""
    if context_sections != [] and issue_comments:
        comments_block = "\n\n" + _format_comments_block(issue_comments)

    feedback_block = ""
    human_feedback = state.get("human_feedback", "")
    if human_feedback:
        feedback_block = "\n\n" + _format_feedback_block(human_feedback)

    original_body = state.get("issue_body", "")
    original_block = ""
    if original_body and original_body.strip() != current_body.strip():
        original_block = (
            "\n\n---\n**Original issue body (before Phase 1 enrichment):**\n"
            + original_body
        )

    user_prompt = (
        f"Issue #{state['issue_number']}: "
        f"{issue_data.get('title', state.get('issue_title', ''))}\n\n"
        f"{scoped_body}"
        f"{comments_block}"
        f"{original_block}"
        f"{feedback_block}\n\n"
        f"Fill in the '{section_name}' section."
    )

    agent_config, user_prompt = _apply_workspace_context(
        agent_config, agent_name, state["repo"], user_prompt
    )

    backend = create_backend(agent_config)
    start_ms = time.monotonic_ns() // 1_000_000
    response = backend.invoke(LLMRequest(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=agent_config.get("temperature", 0.1),
        max_tokens=agent_config.get("max_tokens", 4000),
    ))
    duration_ms = (time.monotonic_ns() // 1_000_000) - start_ms

    validation = validate_response(agent_name, section_name, response.content)
    section_content = validation.cleaned or response.content

    audit: dict = {
        "tokens_used": (response.input_tokens or 0) + (response.output_tokens or 0),
        "cost_usd": response.cost_usd,
        "section": agent_name,
        "content_after": section_content,
        "backend": response.backend,
        "model": response.model,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "duration_ms": duration_ms,
    }
    return section_content, audit


def enrichment_step(
    state: dict,
    agent_name: str,
    section_name: str,
) -> dict:
    """Execute one enrichment step: read → LLM → update → comment → log.

    Args:
        state: Current CortexState dict.
        agent_name: Agent key in agents.yaml (e.g. "mockup", "specify").
        section_name: Issue section to update (e.g. "Technical Specification").

    Returns:
        Dict of state updates (decisions list appended).
    """
    # Normalise state shape for DAP python-func compatibility (#286).
    from cortex.adapters.pipeline_state import from_pipeline_state
    state = from_pipeline_state(state)

    settings = load_settings()
    agent_config = get_agent_backend_config(agent_name)
    token = settings.get_github_token(agent_config["github_token_role"])

    # 1. Read current issue from GitHub
    issue_data = read_issue.invoke({
        "repo": state["repo"],
        "issue_number": state["issue_number"],
        "token": token,
    })
    current_body = issue_data.get("body", "")
    issue_comments = issue_data.get("comments", [])
    content_before = get_issue_section(current_body, section_name)

    # 2. Build prompt and call LLM
    system_prompt = agent_config.get("system_prompt", "")
    # Inject project context.md into system prompt when available (#268).
    # This gives Phase 1 agents knowledge of the target repo's stack and
    # conventions instead of guessing from the issue text alone.
    try:
        from cortex.init.profile import context_path
        _ctx_file = context_path(state["repo"])
        if _ctx_file.is_file():
            _ctx = _ctx_file.read_text(encoding="utf-8", errors="replace")
            if _ctx.strip():
                system_prompt = _ctx.strip() + "\n\n---\n\n" + system_prompt
    except Exception:
        pass
    # Scope the issue body to only the sections this agent needs (issue #26)
    context_sections = agent_config.get("context_sections", "all")
    scoped_body = _scope_issue_context(current_body, context_sections)

    # Include comments only when agent has context_sections (issue #95)
    comments_block = ""
    if context_sections != [] and issue_comments:
        comments_block = "\n\n" + _format_comments_block(issue_comments)

    # Include operator feedback from a previous rejected run (#108).
    # Every Phase 1 agent — including mockup — sees the feedback. The
    # original PR #114 design excluded mockup ("its job is to structure
    # the original report"), but the 2026-04-26 #117 dogfood proved
    # rejection feedback often targets the AC / Description structure
    # itself; if mockup doesn't see it, the re-run reproduces the same
    # hallucinated AC. Cleared by ``finalize`` so it doesn't leak into
    # Phase 2 or later runs (#119).
    feedback_block = ""
    human_feedback = state.get("human_feedback", "")
    if human_feedback:
        feedback_block = "\n\n" + _format_feedback_block(human_feedback)

    # #122: inject the original issue body when it differs from the current
    # enriched body so downstream Phase 1 agents (specify, cicd, dispatcher,
    # finalize) can ground themselves against the operator's original intent.
    original_body = state.get("issue_body", "")
    original_block = ""
    if original_body and original_body.strip() != current_body.strip():
        original_block = (
            "\n\n---\n**Original issue body (before Phase 1 enrichment):**\n"
            + original_body
        )

    user_prompt = (
        f"Issue #{state['issue_number']}: "
        f"{issue_data.get('title', state.get('issue_title', ''))}\n\n"
        f"{scoped_body}"
        f"{comments_block}"
        f"{original_block}"
        f"{feedback_block}\n\n"
        f"Fill in the '{section_name}' section."
    )

    # Inject workspace context (project memory + agent memory) if available
    agent_config, user_prompt = _apply_workspace_context(
        agent_config, agent_name, state["repo"], user_prompt
    )

    backend = create_backend(agent_config)
    start_ms = time.monotonic_ns() // 1_000_000
    response = backend.invoke(LLMRequest(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=agent_config.get("temperature", 0.1),
        max_tokens=agent_config.get("max_tokens", 4000),
    ))
    duration_ms = (time.monotonic_ns() // 1_000_000) - start_ms

    # 2b. Validate + clean the response (issue #27)
    validation = validate_response(agent_name, section_name, response.content)
    if not validation.valid:
        logger.warning(
            "Agent %s response failed validation for %s: %s",
            agent_name, section_name, validation.reason,
        )
    # Use the cleaned content (preamble stripped) when available, else raw
    section_content = validation.cleaned or response.content

    # 3. Update the section in the issue body — fail loud (issue #43)
    new_body = update_issue_section(current_body, section_name, section_content)
    update_result = update_issue_body.invoke({
        "repo": state["repo"],
        "issue_number": state["issue_number"],
        "body": new_body,
        "token": token,
    })
    update_failed = isinstance(update_result, dict) and "error" in update_result
    if update_failed:
        err_msg = update_result.get("error", "unknown error")
        logger.error(
            "Agent %s failed to update_issue_body for section %r (token role=%s): %s",
            agent_name, section_name,
            agent_config.get("github_token_role", "?"), err_msg,
        )
        raise RuntimeError(
            f"{agent_name}: GitHub write failed for section {section_name!r}: {err_msg}"
        )

    # 4. Post completion comment (non-blocking — comment failure logs WARNING)
    words_before = len(content_before.split()) if content_before else 0
    words_after = len(section_content.split()) if section_content else 0
    enrichment_comment = (
        f"**{agent_name}** completed {section_name} "
        f"({words_before} → {words_after} words)"
    )
    comment_url = create_issue_comment.invoke({
        "repo": state["repo"],
        "issue_number": state["issue_number"],
        "body": enrichment_comment,
        "token": token,
    })
    comment_failed = (
        isinstance(comment_url, str) and comment_url.startswith("error:")
    )
    if comment_failed:
        logger.warning(
            "Agent %s failed to post completion comment (token role=%s): %s",
            agent_name, agent_config.get("github_token_role", "?"), comment_url,
        )

    # 5. Audit logging (skip if no run_id, OR if the issue update failed —
    # don't lie in the database about content that never landed on GitHub)
    run_id = state.get("run_id", "")
    if run_id:
        log_decision(
            run_id=run_id,
            agent=agent_name,
            backend=response.backend,
            model=response.model,
            phase="enrichment",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response=response.content,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            project_id=state.get("project_id"),
            cost_usd=response.cost_usd,
            duration_ms=duration_ms,
        )
        if not update_failed:
            log_issue_update(
                run_id=run_id,
                agent=agent_name,
                issue_number=state["issue_number"],
                section=section_name,
                content_before=content_before,
                content_after=response.content,
                github_comment_url=comment_url if not comment_failed else None,
            )

    # 6. Return state updates
    action_label = (
        f"enriched {section_name}" if not update_failed
        else f"FAILED to enrich {section_name} (update_issue_body 403)"
    )
    state_update: dict = {
        "issue_comments": issue_comments,
        "decisions": state.get("decisions", []) + [{
            "node": agent_name,
            "action": action_label,
            "reasoning": response.content[:200],
            "backend": response.backend,
            "model": response.model,
            "tokens": f"{response.input_tokens}in/{response.output_tokens}out",
            "timestamp": datetime.now(UTC).isoformat(),
        }],
        # Full LLM output for callers that need to parse structured content
        # (e.g. dispatcher reads the assignment table). The `reasoning` field
        # above is truncated to 200 chars for audit storage. Issue #70.
        # Underscore prefix marks this as a transient signal — callers should
        # consume it locally and pop it before returning to LangGraph state.
        "_full_response_content": response.content,
    }
    if update_failed:
        state_update["error"] = (
            f"{agent_name} could not update_issue_body for "
            f"{section_name!r}: {update_result.get('error', '?')}"
        )
    return state_update
