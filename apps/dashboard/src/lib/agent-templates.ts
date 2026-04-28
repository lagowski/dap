/**
 * Built-in agent templates (#93) — quick-start library on /agents/new.
 *
 * Templates compress the "read docs/providers.md, decode which
 * runtime + model + role + output_schema match" knowledge into a
 * one-click starter. Selecting one pre-fills the agent form; the
 * user edits and saves a real agent. No DB rows, no template/agent
 * split — adding a template is a code change here.
 *
 * Add a new entry by:
 * 1. Picking a stable ``id`` (used as the picker key).
 * 2. Mirroring the recipe in ``docs/providers.md`` so the surfaces stay aligned.
 * 3. Setting ``output_schema`` matching the role (mirror of
 *    ``ROLE_FIELDS`` from ``packages/types/src/dap_types/role_outputs.py``)
 *    so the cohesion checks in #59 pass out-of-the-box.
 */

export type AgentTemplateCategory =
  | "Implementation"
  | "Test authoring"
  | "Verification"
  | "Selection"
  | "Custom";

export interface AgentTemplate {
  id: string;
  name: string;
  description: string;
  category: AgentTemplateCategory;
  // Values match the form's ``AgentFormValues`` shape — kept inline
  // (not imported) to avoid a circular module reference.
  role: string;
  runtime_id: string;
  runtime_config: Record<string, unknown>;
  prompt_template: string;
  input_schema: string[];
  output_schema: string[];
}

const PROMPT_IMPLEMENTER = `<agent_prompt version="1">
  <role>{{ role }}</role>
  <task>
    Implement the work described in implementation_notes for the
    selected issue ids. Write the changes; describe modified files in
    your response.
  </task>
  <constraints>
    Edit only files listed in implementation_notes when present.
    Keep changes scoped to the issue.
  </constraints>
</agent_prompt>`;

const PROMPT_TEST_AUTHOR = `<agent_prompt version="1">
  <role>{{ role }}</role>
  <task>
    Generate failing tests for the selected_issue_ids. Tests must
    fail before the implementation lands and pass after.
  </task>
  <constraints>
    Do NOT edit production code — only files under tests/.
    Follow the project's existing test framework conventions.
  </constraints>
</agent_prompt>`;

const PROMPT_VERIFIER = `<agent_prompt version="1">
  <role>{{ role }}</role>
  <task>
    Verify the implementation against the tests. Report
    verification_status (approved | rejected | pending) and
    verification_reason.
  </task>
</agent_prompt>`;

const PROMPT_TASK_SELECTOR = `<agent_prompt version="1">
  <role>{{ role }}</role>
  <task>
    Pick the most aligned issues from available_issues. Return
    selected_issue_ids — a subset that maximises user goal alignment
    while respecting max_attempts budget.
  </task>
</agent_prompt>`;

const PROMPT_BASH_RUNNER = `<agent_prompt version="1">
  <role>{{ role }}</role>
  <command>{{ command | default('echo configure your command here') }}</command>
</agent_prompt>`;

export const AGENT_TEMPLATES: readonly AgentTemplate[] = [
  // -- Implementation -----------------------------------------------------
  {
    id: "developer-jr-glm",
    name: "DeveloperJr — GLM via OpenAI-compat",
    description:
      "Cheap, fast api-call backed by GLM 5 over OpenAI-compatible endpoint. Single-shot, no tools — best for trivial tasks where speed and cost beat agentic exploration.",
    category: "Implementation",
    role: "implementer",
    runtime_id: "api-call",
    runtime_config: {
      provider: "openai-compat",
      model_id: "glm-5-flash",
      base_url: "https://api.z.ai/api/coding/paas/v4",
      api_key_env: "GLM_API_KEY",
      max_tokens: 4096,
    },
    prompt_template: PROMPT_IMPLEMENTER,
    input_schema: ["selected_issue_ids", "implementation_notes"],
    output_schema: ["modified_files", "implementation_notes"],
  },
  {
    id: "developer-senior-claude-code",
    name: "DeveloperSenior — Claude Opus 4.7 via Claude Code CLI",
    description:
      "Full agentic loop with file edits, bash, MCP tools. Use for non-trivial implementation where the agent needs to read files, edit, and run tests. ~10-100x cost vs api-call.",
    category: "Implementation",
    role: "implementer",
    runtime_id: "claude-code",
    runtime_config: {
      model_id: "claude-opus-4-7",
      extra_args: ["--allowed-tools", "Read,Edit,Bash,Grep"],
    },
    prompt_template: PROMPT_IMPLEMENTER,
    input_schema: ["selected_issue_ids", "implementation_notes"],
    output_schema: ["modified_files", "implementation_notes"],
  },
  {
    id: "developer-frontend-gemini",
    name: "DeveloperFrontend — Gemini 3 Pro via gemini-cli",
    description:
      "Google's agentic CLI for front-end work — strong on component generation. Reads GEMINI_API_KEY (or GOOGLE_API_KEY) from engine env.",
    category: "Implementation",
    role: "implementer",
    runtime_id: "gemini-cli",
    runtime_config: {
      model_id: "gemini-3.0-pro",
      thinking_budget: 8192,
    },
    prompt_template: PROMPT_IMPLEMENTER,
    input_schema: ["selected_issue_ids", "implementation_notes"],
    output_schema: ["modified_files", "implementation_notes"],
  },

  // -- Test authoring -----------------------------------------------------
  {
    id: "test-author-anthropic",
    name: "TestAuthor — Anthropic Sonnet via api-call",
    description:
      "Generates failing tests for selected issues. Single-shot api-call — deterministic, cheap, no tool use. Constraints in the prompt forbid touching production code.",
    category: "Test authoring",
    role: "test_author",
    runtime_id: "api-call",
    runtime_config: {
      provider: "anthropic",
      model_id: "claude-sonnet-4-6",
      max_tokens: 4096,
      prompt_cache: true,
    },
    prompt_template: PROMPT_TEST_AUTHOR,
    input_schema: ["selected_issue_ids", "available_issues"],
    output_schema: ["tests_generated", "test_files", "test_generation_errors"],
  },

  // -- Verification -------------------------------------------------------
  {
    id: "verifier-anthropic",
    name: "Verifier — Anthropic Haiku via api-call",
    description:
      "Cheap verifier that decides approved / rejected based on test outcomes + modified files. Pairs naturally with the implementer/test_author roles.",
    category: "Verification",
    role: "verifier",
    runtime_id: "api-call",
    runtime_config: {
      provider: "anthropic",
      model_id: "claude-haiku-4-5",
      max_tokens: 2048,
    },
    prompt_template: PROMPT_VERIFIER,
    input_schema: [
      "tests_passed",
      "last_test_output",
      "modified_files",
      "implementation_notes",
    ],
    output_schema: [
      "verification_status",
      "verification_reason",
      "tests_passed",
      "last_test_output",
    ],
  },

  // -- Selection ----------------------------------------------------------
  {
    id: "task-selector-anthropic",
    name: "TaskSelector — Anthropic Haiku via api-call",
    description:
      "Picks subset of available_issues to work on. Cheap single-shot — runs at the head of pipelines that branch on which issues to tackle.",
    category: "Selection",
    role: "task_selector",
    runtime_id: "api-call",
    runtime_config: {
      provider: "anthropic",
      model_id: "claude-haiku-4-5",
      max_tokens: 1024,
    },
    prompt_template: PROMPT_TASK_SELECTOR,
    input_schema: ["available_issues"],
    output_schema: ["selected_issue_ids"],
  },

  // -- Custom -------------------------------------------------------------
  {
    id: "bash-runner",
    name: "BashRunner — shell command",
    description:
      "Runs a single shell command in the run's working directory. No LLM. Good for deterministic steps — pytest, ruff, git, build scripts.",
    category: "Custom",
    role: "bash_runner",
    runtime_id: "bash",
    runtime_config: {
      shell: "/bin/bash",
    },
    prompt_template: PROMPT_BASH_RUNNER,
    input_schema: [],
    output_schema: [],
  },
] as const;

export const AGENT_TEMPLATE_CATEGORIES: readonly AgentTemplateCategory[] = [
  "Implementation",
  "Test authoring",
  "Verification",
  "Selection",
  "Custom",
] as const;

export function findAgentTemplate(id: string): AgentTemplate | undefined {
  return AGENT_TEMPLATES.find((t) => t.id === id);
}
