<!--
Source of truth for the config assistant's grounding corpus.
Generated into apps/engine/src/dap_engine/assistant/docs_corpus.py by
scripts/generate-assistant-corpus.py — edit THIS file, then regenerate
(CI fails if the two drift). This leading comment is stripped before the
content is stuffed into the prompt. Keep it concise — a grounding summary,
not the full docs; update it when the configuration surface changes.
-->
# DAP configuration reference (for the assistant)

DAP runs deterministic agent pipelines. The configurable surface:

## Agents
An agent = a unit of work. Fields:
- role: one of task_selector, prompt_builder, test_author, implementer, verifier, post_check.
- runtime_id: which adapter runs it (see Runtimes).
- runtime_config: adapter-specific (model, provider, command, callable_path, env…).
- prompt_template: a Jinja/XML `<agent_prompt>` that can reference state via {{ field }}.
- input_schema / output_schema: the state fields the agent reads / produces (its CONTRACT).
  A node's input_schema must be produced by an upstream node, or the run fails.
- budget_limit_usd, timeout_ms.

## Runtimes (runtime_id)
- api-call: direct LLM API call. runtime_config: provider (anthropic|openai|gemini|glm|
  openrouter|openai-compat) + model_id + optional system_prompt, max_tokens. Cheapest/simplest
  for text tasks (classify, review, summarize). Needs the provider's API key as an instance env var.
- claude-code: Anthropic Claude Code CLI (agentic coding, file edits). Heavier; good implementer.
- gemini-cli: Google Gemini CLI. codex: OpenAI Codex CLI. aider: aider coding CLI.
- bash: run a shell command (deterministic, free). runtime_config: command, shell, env.
- http: call an HTTP endpoint. python-func: run a Python callable (runtime_config.callable_path
  = "module:func"); the package must be installed in the engine venv (e.g. dap-cortex).

## Pipelines
- nodes (each binds an agent) + edges (data/flow). entry_point names the first node.
- Edges can carry conditions (branch/finalize routing). A node's output_schema should cover the
  next node's input_schema (cohesion) or the field won't flow.
- Gates: approval_required_nodes lists nodes that pause for human approval (human gate) vs
  auto-gates that pass automatically. requires_terminal_final_status enforces a terminal status.
- defaults: max_attempts, budget_limit_usd, approval_required_nodes.

## Providers / secrets
- API keys live as INSTANCE ENV VARS (Settings → environment variables), encrypted at rest.
  Reference them by NAME (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, GLM_API_KEY,
  OPENROUTER_API_KEY). Never put a key value in a prompt or agent config.
- The config assistant itself picks the first provider whose key is set (priority anthropic →
  openai → gemini → glm → openrouter). Override with DAP_ASSISTANT_PROVIDER / DAP_ASSISTANT_MODEL.
  Set DAP_ASSISTANT_PROVIDER=claude-code to run it on the Claude CLI subscription ($0).

## Built-in templates (starting points)
- "Hello world — bash echo": a free bash agent. Good first run.
- "TDD loop — Anthropic": test_author + implementer + verifier loop.
- "Implement only — GLM": single cheap implementer.
- Cortex GitHub-issue pipeline: 14 python-func nodes (needs dap-cortex installed + CORTEX_* env).

## Cost guidance
- Cheap/deterministic: bash, python-func (free), or api-call with a small model (haiku/flash/mini).
- Quality coding: claude-code (implementer), or api-call with a strong model.
