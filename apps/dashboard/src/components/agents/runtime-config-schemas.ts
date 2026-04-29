/**
 * Per-runtime config field schemas for the AgentForm UI.
 *
 * Two truth sources today:
 *
 * - **api-call**, **bash**, **claude-code**, **codex**, **gemini-cli**,
 *   **http** are fully implemented in the engine. Their schemas mirror
 *   the adapters' real `validate_config`. Required fields here match
 *   what the adapter rejects.
 * - **aider** is an F0 stub whose `execute()` raises
 *   `NotImplementedError`. Its schema below is *aspirational* —
 *   it documents the shape a future PR will implement. To avoid
 *   blocking agent creation in the UI before the engine accepts those
 *   configs, stub-runtime fields are NOT marked `required`. Tighten
 *   them once the corresponding adapter lands.
 *
 * Adding a runtime: drop a new entry below. The form picks it up
 * automatically and renders the right inputs.
 */

export type RuntimeFieldKind =
  | "text"
  | "number"
  | "boolean"
  | "select"
  | "json"
  | "kv";

export interface RuntimeFieldOption {
  value: string;
  label: string;
}

export interface RuntimeField {
  key: string;
  label: string;
  kind: RuntimeFieldKind;
  /** Required for submit. Empty string / undefined fails validation. */
  required?: boolean;
  /** Default value populated when the runtime is first selected. */
  default?: string | number | boolean | Record<string, unknown> | unknown[];
  /** Hint text shown below the input. */
  description?: string;
  /** Options for `kind === "select"`. */
  options?: RuntimeFieldOption[];
  /** Placeholder for text/json/number inputs. */
  placeholder?: string;
  /**
   * Show only when this predicate over the current config returns true.
   * Used by api-call to gate `base_url` on `provider === "openai-compat"`.
   */
  visible?: (config: Record<string, unknown>) => boolean;
}

export interface RuntimeConfigSchema {
  /** runtime_id this schema applies to. */
  runtime_id: string;
  fields: RuntimeField[];
}

const API_CALL_PROVIDERS: RuntimeFieldOption[] = [
  { value: "anthropic", label: "Anthropic (Claude)" },
  { value: "openai", label: "OpenAI (GPT-5, o-series)" },
  {
    value: "openai-compat",
    label: "OpenAI-compatible (GLM, Together, OpenRouter, …)",
  },
  { value: "gemini", label: "Google Gemini" },
];

const EFFORT_OPTIONS: RuntimeFieldOption[] = [
  { value: "low", label: "low" },
  { value: "medium", label: "medium" },
  { value: "high", label: "high" },
  { value: "xhigh", label: "xhigh (Opus 4.7)" },
  { value: "max", label: "max (Opus only)" },
];

const isProvider =
  (...wanted: string[]) =>
  (config: Record<string, unknown>): boolean =>
    wanted.includes(String(config.provider ?? "anthropic"));

/**
 * Ordered list of runtime ids the form / Test panel surface in
 * dropdowns. Single source of truth — both ``AgentForm`` and the
 * compare-mode editor in ``AgentTestPanel`` import this so they
 * can't drift if a runtime is added or removed.
 */
export const AGENT_RUNTIME_IDS = [
  "api-call",
  "claude-code",
  "gemini-cli",
  "codex",
  "aider",
  "bash",
  "http",
] as const;

export type AgentRuntimeId = (typeof AGENT_RUNTIME_IDS)[number];

export const RUNTIME_SCHEMAS: Record<string, RuntimeConfigSchema> = {
  "api-call": {
    runtime_id: "api-call",
    fields: [
      {
        key: "provider",
        label: "Provider",
        kind: "select",
        required: true,
        default: "anthropic",
        options: API_CALL_PROVIDERS,
        description:
          "Selects the SDK call path. Each provider reads its own env var for the API key.",
      },
      {
        key: "model_id",
        label: "Model ID",
        kind: "text",
        required: true,
        placeholder: "claude-haiku-4-5",
        description:
          "Provider-specific model identifier (e.g. claude-opus-4-7, gpt-5-mini, gemini-3.0-pro, glm-5-flash).",
      },
      {
        key: "max_tokens",
        label: "Max tokens",
        kind: "number",
        default: 4096,
        description: "Per-call output token limit.",
      },
      {
        key: "base_url",
        label: "Base URL",
        kind: "text",
        required: true,
        placeholder: "https://api.z.ai/api/coding/paas/v4",
        description:
          "OpenAI-compatible endpoint. Required for openai-compat (GLM, Together, OpenRouter, …).",
        visible: isProvider("openai-compat"),
      },
      {
        key: "api_key_env",
        label: "API key env var name",
        kind: "text",
        required: true,
        placeholder: "GLM_API_KEY",
        description:
          "Name of the env var holding the API key — engine reads the value at call time, never persisted in DB.",
        visible: isProvider("openai-compat"),
      },
      {
        key: "effort",
        label: "Reasoning effort",
        kind: "select",
        options: [{ value: "", label: "(default)" }, ...EFFORT_OPTIONS],
        description: "Anthropic only. Controls reasoning depth + token spend.",
        visible: isProvider("anthropic"),
      },
      {
        key: "enable_thinking",
        label: "Enable adaptive thinking",
        kind: "boolean",
        default: false,
        description: "Anthropic only. Enables thinking blocks for Claude 4.x.",
        visible: isProvider("anthropic"),
      },
      {
        key: "prompt_cache",
        label: "Enable prompt caching",
        kind: "boolean",
        default: false,
        description: "Anthropic only. Adds ephemeral cache_control to the system prompt.",
        visible: isProvider("anthropic"),
      },
      {
        key: "temperature",
        label: "Temperature",
        kind: "number",
        description: "Optional. OpenAI / Gemini.",
        visible: isProvider("openai", "openai-compat", "gemini"),
      },
      {
        key: "thinking_budget",
        label: "Thinking budget",
        kind: "number",
        description: "Gemini only. Max tokens spent on internal reasoning.",
        visible: isProvider("gemini"),
      },
      {
        key: "system_prompt",
        label: "Extra system prompt",
        kind: "text",
        description:
          "Optional. Prepended to the rendered XML in the system message.",
      },
    ],
  },

  bash: {
    runtime_id: "bash",
    fields: [
      {
        key: "command",
        label: "Command",
        kind: "text",
        placeholder: "echo hello",
        description:
          "Shell command. If empty, the engine extracts the first <command>...</command> from the prompt template.",
      },
      {
        key: "shell",
        label: "Shell",
        kind: "text",
        default: "/bin/bash",
        description:
          "Shell binary (also overridable via DAP_BASH_SHELL env). Defaults to /bin/bash.",
      },
      {
        key: "env",
        label: "Environment overrides",
        kind: "kv",
        description:
          "Extra env vars merged into the subprocess environment (after engine env, before the agent's own values).",
      },
    ],
  },

  "claude-code": {
    runtime_id: "claude-code",
    fields: [
      {
        key: "model_id",
        label: "Model ID",
        kind: "text",
        required: true,
        placeholder: "claude-opus-4-7",
        description:
          "Required. Anthropic model name. Reads ANTHROPIC_API_KEY from the engine's environment.",
      },
      {
        key: "binary_path",
        label: "Binary path",
        kind: "text",
        placeholder: "/opt/homebrew/bin/claude",
        description: "Defaults to `claude` on PATH. Override if you have multiple installs.",
      },
      {
        key: "extra_args",
        label: "Extra CLI args",
        kind: "json",
        description:
          'JSON array of strings appended to the claude CLI invocation. Example: ["--allowed-tools","Read,Edit,Bash"]',
        placeholder: '["--allowed-tools", "Read,Edit"]',
      },
    ],
  },

  "gemini-cli": {
    runtime_id: "gemini-cli",
    fields: [
      {
        key: "model_id",
        label: "Model ID",
        kind: "text",
        required: true,
        placeholder: "gemini-3.0-pro",
        description:
          "Required. Gemini model name. CLI reads GEMINI_API_KEY (or GOOGLE_API_KEY) from the engine's environment.",
      },
      {
        key: "binary_path",
        label: "Binary path",
        kind: "text",
        placeholder: "/opt/homebrew/bin/gemini",
        description: "Defaults to `gemini` on PATH.",
      },
      {
        key: "thinking_budget",
        label: "Thinking budget",
        kind: "number",
        description: "Optional. Max tokens spent on internal reasoning.",
      },
    ],
  },

  codex: {
    runtime_id: "codex",
    fields: [
      {
        key: "model_id",
        label: "Model ID",
        kind: "text",
        required: true,
        placeholder: "gpt-5-codex",
        description:
          "Required. OpenAI model name. CLI reads OPENAI_API_KEY from the engine's environment.",
      },
      {
        key: "binary_path",
        label: "Binary path",
        kind: "text",
        placeholder: "/opt/homebrew/bin/codex",
        description: "Defaults to `codex` on PATH.",
      },
      {
        key: "extra_args",
        label: "Extra CLI args",
        kind: "json",
        description:
          'JSON array of strings appended to the codex CLI invocation. Example: ["--sandbox", "workspace-write"]',
        placeholder: '["--sandbox", "workspace-write"]',
      },
    ],
  },

  http: {
    runtime_id: "http",
    fields: [
      {
        key: "url",
        label: "URL",
        kind: "text",
        required: true,
        placeholder: "http://localhost:11434/api/generate",
        description:
          "Required. Endpoint URL. Must start with http:// or https://.",
      },
      {
        key: "method",
        label: "Method",
        kind: "select",
        default: "POST",
        options: [
          { value: "POST", label: "POST" },
          { value: "PUT", label: "PUT" },
        ],
      },
      {
        key: "request_template",
        label: "Request body template",
        kind: "json",
        required: true,
        description:
          'Required. JSON template rendered with prompt_xml in scope. Example: {"model": "{{ runtime_config.model_id }}", "prompt": "{{ prompt_xml }}", "stream": false}',
      },
      {
        key: "response_extractor",
        label: "Response extractor",
        kind: "json",
        required: true,
        description:
          'Required. JSONPath map; must include "output". Example: {"output": "$.response", "tokens_used": "$.eval_count"}',
      },
      {
        key: "auth",
        label: "Auth",
        kind: "json",
        description:
          'Optional. {"type": "bearer", "env": "OLLAMA_API_KEY"} | {"type": "header", "name": "X-API-Key", "env": "..."} | {"type": "basic", "user_env": "...", "pass_env": "..."}',
      },
      {
        key: "headers",
        label: "Static headers",
        kind: "json",
        description: "Optional dict of static headers sent with every request.",
      },
    ],
  },

  // STUB — aider adapter is F0-stub. No issue tracked yet — fields are
  // a placeholder so users can still pick the runtime in the form.
  aider: {
    runtime_id: "aider",
    fields: [
      {
        key: "model_id",
        label: "Model ID",
        kind: "text",
        description:
          "Aspirational schema — adapter is a stub. Fields not enforced yet.",
      },
      {
        key: "binary_path",
        label: "Binary path",
        kind: "text",
      },
    ],
  },
};

/**
 * Compute defaults for a runtime — used when user switches runtime in the
 * form so required fields aren't left undefined.
 */
export function defaultRuntimeConfig(
  runtime_id: string,
): Record<string, unknown> {
  const schema = RUNTIME_SCHEMAS[runtime_id];
  if (!schema) return {};
  const out: Record<string, unknown> = {};
  for (const field of schema.fields) {
    if (field.default !== undefined) {
      out[field.key] = field.default;
    }
  }
  return out;
}

/**
 * Strip keys that the schema doesn't declare — applied at submit time so
 * stale fields from a previous runtime selection don't leak into the
 * payload. Unknown runtimes pass through unchanged.
 */
export function pruneRuntimeConfig(
  runtime_id: string,
  config: Record<string, unknown>,
): Record<string, unknown> {
  const schema = RUNTIME_SCHEMAS[runtime_id];
  if (!schema) return config;
  const allowed = new Set(schema.fields.map((f) => f.key));
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(config)) {
    if (allowed.has(k)) {
      // Skip empty strings / nulls so we don't ship "" for optional text fields.
      if (v === "" || v == null) continue;
      out[k] = v;
    }
  }
  return out;
}

/**
 * Validate the user's input against the runtime's required fields. Returns
 * a list of field-level error strings (empty when valid).
 */
export function validateRuntimeConfig(
  runtime_id: string,
  config: Record<string, unknown>,
): { key: string; message: string }[] {
  const schema = RUNTIME_SCHEMAS[runtime_id];
  if (!schema) return [];
  const errors: { key: string; message: string }[] = [];
  for (const field of schema.fields) {
    const visible = field.visible ? field.visible(config) : true;
    if (!visible) continue;
    if (!field.required) continue;
    const value = config[field.key];
    if (value === undefined || value === null || value === "") {
      errors.push({ key: field.key, message: `${field.label} is required` });
    }
  }
  return errors;
}
