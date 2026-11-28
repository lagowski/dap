/**
 * Built-in pipeline templates (#128) — quick-start library on /pipelines/new.
 *
 * Each template is a pre-built ``PipelineExport`` bundle: the
 * pipeline structure plus the agents it needs. Picking a template
 * runs ``POST /pipelines/import`` directly — the engine creates the
 * agents, builds the ``old_id → new_id`` remap, and persists the
 * pipeline. The user lands on ``/pipelines/{id}/edit`` and can
 * customise from there.
 *
 * Templates stay code-driven (same model as ``agent-templates.ts``):
 * no DB rows, no template/instance split. Adding a new entry is a
 * code change in this file.
 *
 * Add a new entry by:
 * 1. Picking a stable ``id`` (used as the picker key — never rename
 *    once it ships).
 * 2. Crafting a ``bundle`` whose ``pipeline.nodes[].agent_id`` strings
 *    match the keys in ``bundled_agents``. The importer rewrites them
 *    to fresh local ids on save; what matters here is internal
 *    consistency.
 * 3. Making sure each agent's ``input_schema`` / ``output_schema``
 *    satisfies the cohesion rules so the DAG validator accepts the
 *    bundle on import. **In particular**: an entry node (or any node
 *    upstream-of-which the field isn't written) cannot declare an
 *    ``input_schema`` field whose ``PipelineState`` default is in
 *    ``_TRIVIAL_DEFAULTS`` (None / False / 0 / "" / [] / {}). The
 *    cohesion check rejects a pipeline like that with
 *    "requires X but no upstream node writes it". Templates here
 *    keep ``input_schema`` empty (legacy mode — prompts can still
 *    reference any state field via Jinja) so a small DAG validates
 *    cleanly out of the box; tightening contracts is a deliberate
 *    follow-up the user does after import.
 */

import type { PipelineExport } from "./api/types";

export type PipelineTemplateCategory =
  | "Examples"
  | "Development"
  | "Implementation"
  | "Cortex"
  | "Custom";

export interface PipelineTemplate {
  /** Stable picker key. Don't rename once shipped — operators link to it. */
  id: string;
  /** Display name in the picker card. */
  name: string;
  /** One-paragraph description shown in the card body. */
  description: string;
  /** Category bucket — drives the picker's section grouping. */
  category: PipelineTemplateCategory;
  /**
   * The actual portable bundle the importer consumes. Each
   * referenced ``node.agent_id`` must be a key in
   * ``bundle.bundled_agents`` (the importer remaps to fresh local
   * ids). Treat the keys as intra-template tokens, not real ids.
   */
  bundle: PipelineExport;
}

// ---------------------------------------------------------------------------
// Shared agent payloads — keep prompts here so multiple templates can
// reuse them without copy-paste. Mirrors the convention in
// ``agent-templates.ts`` for individual prompts.
// ---------------------------------------------------------------------------

const PROMPT_GREETER = `<agent_prompt version="1">
  <role>greeter</role>
  <task>Print a greeting.</task>
</agent_prompt>`;

const PROMPT_IMPLEMENTER = `<agent_prompt version="1">
  <role>{{ role }}</role>
  <task>
    Implement the work described in implementation_notes for the
    selected_issue_ids. Write the changes; describe modified files in
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

// ---------------------------------------------------------------------------
// Template library
// ---------------------------------------------------------------------------

export const PIPELINE_TEMPLATES: readonly PipelineTemplate[] = [
  // -- Examples ----------------------------------------------------------
  {
    id: "hello-world-bash",
    name: "Hello world — bash echo",
    description:
      "Single-node pipeline using the bash adapter to echo a greeting. Free, deterministic, no API key needed. Good first import to confirm the engine + dashboard wiring works end-to-end before you reach for an LLM.",
    category: "Examples",
    bundle: {
      schema_version: "pipeline-export/1",
      pipeline: {
        name: "Hello world pipeline",
        description: "echoes a greeting via the bash adapter",
        schema_version: "langgraph/1.0",
        state_schema_ref: "PipelineState.v1",
        entry_point: "n1",
        nodes: [
          { id: "n1", agent_id: "tpl_hello_greeter", position: { x: 0, y: 0 } },
        ],
        edges: [
          { id: "e1", source: "__start__", target: "n1" },
          { id: "e2", source: "n1", target: "__end__" },
        ],
        defaults: {
          max_attempts: 1,
          budget_limit_usd: 0.0,
          approval_required_nodes: [],
        },
      },
      bundled_agents: {
        tpl_hello_greeter: {
          name: "Hello Greeter",
          role: "post_check",
          runtime_id: "bash",
          runtime_config: {
            shell: "/bin/bash",
            command: "echo 'hello from dap'",
          },
          prompt_template: PROMPT_GREETER,
          input_schema: [],
          output_schema: [],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 5_000,
        },
      },
    },
  },

  // -- Development -------------------------------------------------------
  {
    id: "tdd-loop-anthropic",
    name: "TDD loop — Anthropic Claude family",
    description:
      "test_author → implementer → verifier with a retry edge back to implementer when verification rejects. Three Anthropic agents (Sonnet for tests + impl, Haiku for verification). Burns ~$0.05–0.20 per run depending on context. Edit the agents after import to swap models or adjust prompts.",
    category: "Development",
    bundle: {
      schema_version: "pipeline-export/1",
      pipeline: {
        name: "TDD Pipeline",
        description: "Test-driven implementation loop with verifier",
        schema_version: "langgraph/1.0",
        state_schema_ref: "PipelineState.v1",
        entry_point: "test_author",
        nodes: [
          {
            id: "test_author",
            agent_id: "tpl_tdd_test_author",
            position: { x: 0, y: 0 },
          },
          {
            id: "implementer",
            agent_id: "tpl_tdd_implementer",
            position: { x: 240, y: 0 },
          },
          {
            id: "verifier",
            agent_id: "tpl_tdd_verifier",
            position: { x: 480, y: 0 },
          },
        ],
        edges: [
          { id: "e1", source: "__start__", target: "test_author" },
          { id: "e2", source: "test_author", target: "implementer" },
          { id: "e3", source: "implementer", target: "verifier" },
          {
            id: "e_approved",
            source: "verifier",
            target: "__end__",
            condition: {
              type: "comparison",
              field: "verification_status",
              operator: "==",
              value: "approved",
            },
          },
          {
            id: "e_retry",
            source: "verifier",
            target: "implementer",
            condition: {
              type: "comparison",
              field: "verification_status",
              operator: "==",
              value: "rejected",
            },
          },
        ],
        defaults: {
          max_attempts: 3,
          budget_limit_usd: 1.0,
          approval_required_nodes: [],
        },
      },
      bundled_agents: {
        tpl_tdd_test_author: {
          name: "TestAuthor (Sonnet)",
          role: "test_author",
          runtime_id: "api-call",
          runtime_config: {
            provider: "anthropic",
            model_id: "claude-sonnet-4-6",
            max_tokens: 4096,
            prompt_cache: true,
          },
          prompt_template: PROMPT_TEST_AUTHOR,
          // Empty input_schema = legacy mode: the prompt can still
          // reference any PipelineState field via Jinja, but the
          // DAG validator's cohesion check sees no declared
          // contract and skips this node. Templates ship a working
          // pipeline first; the user tightens contracts after.
          input_schema: [],
          output_schema: [
            "tests_generated",
            "test_files",
            "test_generation_errors",
          ],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 60_000,
        },
        tpl_tdd_implementer: {
          name: "Implementer (Sonnet)",
          role: "implementer",
          runtime_id: "api-call",
          runtime_config: {
            provider: "anthropic",
            model_id: "claude-sonnet-4-6",
            max_tokens: 4096,
            prompt_cache: true,
          },
          prompt_template: PROMPT_IMPLEMENTER,
          input_schema: [],
          output_schema: ["modified_files", "implementation_notes"],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 60_000,
        },
        tpl_tdd_verifier: {
          name: "Verifier (Haiku)",
          role: "verifier",
          runtime_id: "api-call",
          runtime_config: {
            provider: "anthropic",
            model_id: "claude-haiku-4-5",
            max_tokens: 2048,
          },
          prompt_template: PROMPT_VERIFIER,
          input_schema: [],
          output_schema: [
            "verification_status",
            "verification_reason",
            "tests_passed",
            "last_test_output",
          ],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 60_000,
        },
      },
    },
  },

  // -- Implementation ----------------------------------------------------
  {
    id: "implement-only-glm",
    name: "Implement only — Z.AI GLM (cheap)",
    description:
      "Single implementer node using Z.AI's coding model via the first-class glm provider. Cheapest end-to-end pipeline that hits a real LLM. Needs GLM_API_KEY in the engine's env.",
    category: "Implementation",
    bundle: {
      schema_version: "pipeline-export/1",
      pipeline: {
        name: "Implement-only (GLM)",
        description: "Single-node implementer pipeline driven by Z.AI GLM",
        schema_version: "langgraph/1.0",
        state_schema_ref: "PipelineState.v1",
        entry_point: "implementer",
        nodes: [
          {
            id: "implementer",
            agent_id: "tpl_glm_implementer",
            position: { x: 0, y: 0 },
          },
        ],
        edges: [
          { id: "e1", source: "__start__", target: "implementer" },
          { id: "e2", source: "implementer", target: "__end__" },
        ],
        defaults: {
          max_attempts: 1,
          budget_limit_usd: 0.5,
          approval_required_nodes: [],
        },
      },
      bundled_agents: {
        tpl_glm_implementer: {
          name: "Implementer (GLM 4.5)",
          role: "implementer",
          runtime_id: "api-call",
          runtime_config: {
            provider: "glm",
            model_id: "glm-4.5",
            max_tokens: 4096,
          },
          prompt_template: PROMPT_IMPLEMENTER,
          // Legacy mode for the same reason as the TDD template:
          // an entry-point node can't declare ``input_schema``
          // fields whose ``PipelineState`` defaults are trivial
          // (None / [] / "" — see _TRIVIAL_DEFAULTS in the
          // engine validator). Empty list keeps the cohesion
          // check quiet on import; the prompt still reads state
          // via Jinja.
          input_schema: [],
          output_schema: ["modified_files", "implementation_notes"],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 60_000,
        },
      },
    },
  },
  {
    id: "cortex-github-issue",
    name: "Cortex — GitHub issue resolution",
    description:
      "The Cortex issue-resolution workflow: 17 nodes / 14 python-func agents (mockup → specify → cicd → dispatcher → coder/designer/documenter → review → tests → PR → merge) with human-approval gates. Requires the dap-cortex package installed in the engine venv and the CORTEX_* env vars set — without them the imported pipeline's nodes fail at run time.",
    category: "Cortex",
    bundle: {
      schema_version: "pipeline-export/1",
      pipeline: {
        name: "Cortex GitHub Issue Pipeline",
        description:
          "13-agent pipeline: GitHub issue → Phase 1 enrichment → Phase 2 coding → Phase 3 PR merge. Port of the standalone Cortex pipeline to a DAP bundle.",
        schema_version: "langgraph/1.0",
        state_schema_ref: "PipelineState.v1",
        entry_point: "mockup",
        nodes: [
          {
            id: "mockup",
            agent_id: "cx_mockup",
            position: {
              x: 0,
              y: 0,
            },
          },
          {
            id: "specify",
            agent_id: "cx_specify",
            position: {
              x: 220,
              y: 0,
            },
          },
          {
            id: "cicd",
            agent_id: "cx_cicd",
            position: {
              x: 440,
              y: 0,
            },
          },
          {
            id: "dispatcher",
            agent_id: "cx_dispatcher",
            position: {
              x: 660,
              y: 0,
            },
          },
          {
            id: "finalize",
            agent_id: "cx_finalize",
            position: {
              x: 880,
              y: 0,
            },
          },
          {
            id: "needs_work_gate",
            agent_id: "cx_human_gate",
            position: {
              x: 1100,
              y: 160,
            },
          },
          {
            id: "phase1_gate",
            agent_id: "cx_human_gate",
            position: {
              x: 1100,
              y: 0,
            },
          },
          {
            id: "coder",
            agent_id: "cx_coder",
            position: {
              x: 1320,
              y: 0,
            },
          },
          {
            id: "designer",
            agent_id: "cx_designer",
            position: {
              x: 1540,
              y: 0,
            },
          },
          {
            id: "documenter",
            agent_id: "cx_documenter",
            position: {
              x: 1760,
              y: 0,
            },
          },
          {
            id: "phase2_gate",
            agent_id: "cx_human_gate",
            position: {
              x: 1980,
              y: 0,
            },
          },
          {
            id: "code_reviewer",
            agent_id: "cx_code_reviewer",
            position: {
              x: 2200,
              y: 0,
            },
          },
          {
            id: "tester",
            agent_id: "cx_tester",
            position: {
              x: 2420,
              y: 0,
            },
          },
          {
            id: "pr_creator",
            agent_id: "cx_pr_creator",
            position: {
              x: 2640,
              y: 0,
            },
          },
          {
            id: "reviewer",
            agent_id: "cx_reviewer",
            position: {
              x: 2860,
              y: 0,
            },
          },
          {
            id: "phase3_gate",
            agent_id: "cx_human_gate",
            position: {
              x: 3080,
              y: 0,
            },
          },
          {
            id: "pr_merger",
            agent_id: "cx_pr_merger",
            position: {
              x: 3300,
              y: 0,
            },
          },
        ],
        edges: [
          {
            id: "e01",
            source: "__start__",
            target: "mockup",
          },
          {
            id: "e02",
            source: "mockup",
            target: "specify",
          },
          {
            id: "e03",
            source: "specify",
            target: "cicd",
          },
          {
            id: "e04",
            source: "cicd",
            target: "dispatcher",
          },
          {
            id: "e05",
            source: "dispatcher",
            target: "finalize",
          },
          {
            id: "e06a",
            source: "finalize",
            target: "phase1_gate",
            label: "READY",
            condition: {
              type: "comparison",
              field: "extensions.issue_ready",
              operator: "==",
              value: true,
            },
          },
          {
            id: "e06b",
            source: "finalize",
            target: "needs_work_gate",
            label: "NEEDS_WORK",
            condition: {
              type: "comparison",
              field: "extensions.issue_ready",
              operator: "!=",
              value: true,
            },
          },
          {
            id: "e_nwg",
            source: "needs_work_gate",
            target: "specify",
            label: "retry",
          },
          {
            id: "e07",
            source: "phase1_gate",
            target: "coder",
          },
          {
            id: "e08",
            source: "coder",
            target: "designer",
          },
          {
            id: "e09",
            source: "designer",
            target: "documenter",
          },
          {
            id: "e10",
            source: "documenter",
            target: "phase2_gate",
          },
          {
            id: "e11",
            source: "phase2_gate",
            target: "code_reviewer",
          },
          {
            id: "e12",
            source: "code_reviewer",
            target: "tester",
          },
          {
            id: "e13",
            source: "tester",
            target: "pr_creator",
          },
          {
            id: "e14",
            source: "pr_creator",
            target: "reviewer",
          },
          {
            id: "e15",
            source: "reviewer",
            target: "phase3_gate",
          },
          {
            id: "e16",
            source: "phase3_gate",
            target: "pr_merger",
          },
          {
            id: "e17",
            source: "pr_merger",
            target: "__end__",
          },
        ],
        defaults: {
          max_attempts: 3,
          budget_limit_usd: 5.0,
          approval_required_nodes: [
            "needs_work_gate",
            "phase1_gate",
            "phase2_gate",
            "phase3_gate",
          ],
          requires_terminal_final_status: true,
        },
      },
      bundled_agents: {
        cx_mockup: {
          name: "Cortex Mockup",
          role: "prompt_builder",
          runtime_id: "python-func",
          runtime_config: {
            callable_path: "cortex.nodes.mockup:run",
          },
          prompt_template:
            "<agent_prompt><run_id>{{ run_id }}</run_id><repo>{{ repo }}</repo></agent_prompt>",
          input_schema: [],
          output_schema: [],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 120000,
        },
        cx_specify: {
          name: "Cortex Specify",
          role: "prompt_builder",
          runtime_id: "python-func",
          runtime_config: {
            callable_path: "cortex.nodes.specify:run",
          },
          prompt_template:
            "<agent_prompt><run_id>{{ run_id }}</run_id><repo>{{ repo }}</repo></agent_prompt>",
          input_schema: [],
          output_schema: [],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 180000,
        },
        cx_cicd: {
          name: "Cortex CI/CD",
          role: "prompt_builder",
          runtime_id: "python-func",
          runtime_config: {
            callable_path: "cortex.nodes.cicd:run",
          },
          prompt_template:
            "<agent_prompt><run_id>{{ run_id }}</run_id><repo>{{ repo }}</repo></agent_prompt>",
          input_schema: [],
          output_schema: [],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 120000,
        },
        cx_dispatcher: {
          name: "Cortex Dispatcher",
          role: "task_selector",
          runtime_id: "python-func",
          runtime_config: {
            callable_path: "cortex.nodes.dispatcher:run",
          },
          prompt_template:
            "<agent_prompt><run_id>{{ run_id }}</run_id><repo>{{ repo }}</repo></agent_prompt>",
          input_schema: [],
          output_schema: [],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 120000,
        },
        cx_finalize: {
          name: "Cortex Finalize",
          role: "post_check",
          runtime_id: "python-func",
          runtime_config: {
            callable_path: "cortex.nodes.finalize:run",
          },
          prompt_template:
            "<agent_prompt><run_id>{{ run_id }}</run_id><repo>{{ repo }}</repo></agent_prompt>",
          input_schema: [],
          output_schema: ["finalize_warnings"],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 180000,
        },
        cx_human_gate: {
          name: "Human Approval Gate",
          role: "post_check",
          runtime_id: "python-func",
          runtime_config: {
            callable_path: "cortex.dap_steps.human_gate:run",
          },
          prompt_template:
            "<agent_prompt><run_id>{{ run_id }}</run_id><repo>{{ repo }}</repo></agent_prompt>",
          input_schema: [],
          output_schema: [],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 86400000,
        },
        cx_coder: {
          name: "Cortex Coder",
          role: "prompt_builder",
          runtime_id: "python-func",
          runtime_config: {
            callable_path: "cortex.nodes.coder:run",
          },
          prompt_template:
            "<agent_prompt><run_id>{{ run_id }}</run_id><repo>{{ repo }}</repo></agent_prompt>",
          input_schema: [],
          output_schema: [],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 1800000,
        },
        cx_designer: {
          name: "Cortex Designer",
          role: "prompt_builder",
          runtime_id: "python-func",
          runtime_config: {
            callable_path: "cortex.nodes.designer:run",
          },
          prompt_template:
            "<agent_prompt><run_id>{{ run_id }}</run_id><repo>{{ repo }}</repo></agent_prompt>",
          input_schema: [],
          output_schema: [],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 900000,
        },
        cx_documenter: {
          name: "Cortex Documenter",
          role: "prompt_builder",
          runtime_id: "python-func",
          runtime_config: {
            callable_path: "cortex.nodes.documenter:run",
          },
          prompt_template:
            "<agent_prompt><run_id>{{ run_id }}</run_id><repo>{{ repo }}</repo></agent_prompt>",
          input_schema: [],
          output_schema: [],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 600000,
        },
        cx_code_reviewer: {
          name: "Cortex Code Reviewer",
          role: "post_check",
          runtime_id: "python-func",
          runtime_config: {
            callable_path: "cortex.nodes.code_reviewer:run",
          },
          prompt_template:
            "<agent_prompt><run_id>{{ run_id }}</run_id><repo>{{ repo }}</repo></agent_prompt>",
          input_schema: [],
          output_schema: [],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 120000,
        },
        cx_tester: {
          name: "Cortex Tester",
          role: "post_check",
          runtime_id: "python-func",
          runtime_config: {
            callable_path: "cortex.nodes.tester:run",
          },
          prompt_template:
            "<agent_prompt><run_id>{{ run_id }}</run_id><repo>{{ repo }}</repo></agent_prompt>",
          input_schema: [],
          output_schema: [],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 600000,
        },
        cx_pr_creator: {
          name: "Cortex PR Creator",
          role: "post_check",
          runtime_id: "python-func",
          runtime_config: {
            callable_path: "cortex.nodes.pr_creator:run",
          },
          prompt_template:
            "<agent_prompt><run_id>{{ run_id }}</run_id><repo>{{ repo }}</repo></agent_prompt>",
          input_schema: [],
          output_schema: [],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 120000,
        },
        cx_reviewer: {
          name: "Cortex Reviewer",
          role: "post_check",
          runtime_id: "python-func",
          runtime_config: {
            callable_path: "cortex.nodes.reviewer:run",
          },
          prompt_template:
            "<agent_prompt><run_id>{{ run_id }}</run_id><repo>{{ repo }}</repo></agent_prompt>",
          input_schema: [],
          output_schema: [],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 180000,
        },
        cx_pr_merger: {
          name: "Cortex PR Merger",
          role: "post_check",
          runtime_id: "python-func",
          runtime_config: {
            callable_path: "cortex.nodes.pr_merger:run",
          },
          prompt_template:
            "<agent_prompt><run_id>{{ run_id }}</run_id><repo>{{ repo }}</repo></agent_prompt>",
          input_schema: [],
          output_schema: [],
          constraints: [],
          budget_limit_usd: null,
          timeout_ms: 120000,
        },
      },
    },
  },
] as const;

export const PIPELINE_TEMPLATE_CATEGORIES: readonly PipelineTemplateCategory[] =
  ["Examples", "Development", "Implementation", "Cortex", "Custom"] as const;

export function findPipelineTemplate(id: string): PipelineTemplate | undefined {
  return PIPELINE_TEMPLATES.find((t) => t.id === id);
}
