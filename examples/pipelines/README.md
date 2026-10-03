# Example pipeline bundles

Drop-in `.pipeline-bundle.json` files you can import into any DAP installation
via the dashboard's **Import JSON** button on `/pipelines` (or `POST /pipelines/import`
directly). Each bundle ships with the agents it needs — see #126 for the
schema and #127 for the import semantics.

These are reference recipes, not built-ins. Edit the JSON before importing
to swap providers (e.g. `glm` → `anthropic`), tighten prompts, or change
the DAG. Once imported, every agent becomes a real DB row editable from
`/agents/<id>/edit` and the pipeline is editable in the Designer.

## How to use

1. Pick a bundle below and download / copy the JSON.
2. (Optional) edit it — change provider, model, prompts, paths, etc.
3. In the dashboard, open `/pipelines` and click **Import JSON** in the header.
4. Pick the file. The engine creates the bundled agents first, remaps
   every `node.agent_id` to the freshly assigned local ids, and then
   creates the pipeline. You land on `/pipelines/<id>/edit`.
5. Customise per-agent via the agent edit pages, or refine the DAG via
   the Designer. Trigger a run when ready.

## Available bundles

### `github-read-issue-comment.pipeline-bundle.json`

The smallest useful `github` runtime pipeline: read an issue, then comment on
it using what was read. Two nodes, no LLM, no shell, no `python-func`.

```
__start__ → read_issue → comment → __end__
```

| Node | Runtime | What it does |
|---|---|---|
| `read_issue` | github (`op: read_issue`) | Reads `{{ state.extensions.issue_number }}` in `{{ state.repo }}`, writes `extensions.github_issue` |
| `comment` | github (`op: comment`) | Posts "DAP read **<title>** (#N). Labels: …", rendered from `extensions.github_issue` |

**Run it** with an initial state naming the repo and issue:

```json
{"repo": "owner/name", "branch": "main", "extensions": {"issue_number": 42}}
```

**Prerequisites:**

- A token that can read the repo and comment on its issues, in an env var
  named `GH_TOKEN`: in the engine env, as an instance env var
  (`/settings/admin/env-vars`) or as a project env var. To use another
  variable (e.g. a role token like `CORTEX_GH_TOKEN_ISSUES`), change both
  agents' `token_env`. Never put the token itself in the bundle.

**Why `requires_terminal_final_status: false`:** the last node is a `github`
call, which can't set `final_status`. With the default (`true`, since #628)
every run would end `failed` even though both nodes succeeded. See
[`docs/runtimes.md`](../../docs/runtimes.md#the-final_status-contract).

Every op, its params and outputs: [`docs/runtimes.md` → `github`](../../docs/runtimes.md#github-github-operations-as-nodes-920-921).

### `github-issue-triage.pipeline-bundle.json`

Five-node linear pipeline that picks the most sensible open GitHub issue,
enriches it with project PRD context, and creates a `feature/issue-N`
branch — leaving downstream coding to claude-code / IDE.

```
__start__ → fetch_issues → select_issue → load_prd → enrich → create_branch → __end__
```

| Node | Runtime | Role | What it does |
|---|---|---|---|
| `fetch_issues` | bash | post_check | `gh issue list --json …` → writes `available_issues` |
| `select_issue` | api-call (glm) | task_selector | Picks the best issue, writes `selected_issue_ids` |
| `load_prd` | bash | post_check | `cat docs/PRD.md` → writes raw PRD to `implementation_notes` |
| `enrich` | api-call (glm) | prompt_builder | Synthesises a structured brief, overwrites `implementation_notes` |
| `create_branch` | bash | post_check | `git checkout -B feature/issue-N origin/main`, writes `branch` |

**Prerequisites:**

- `gh` CLI installed + `gh auth login` done on the host running the engine.
- `GLM_API_KEY` exported in the engine's env (or edit the JSON / agents to
  swap to `anthropic` / `openai` / `gemini`).
- `docs/PRD.md` in the target repo (1–2 pages enough). Without it the
  `load_prd` node still runs — writes a placeholder so the pipeline doesn't
  crash, but the brief from `enrich` won't have project context.
- Engine started with the target repo as working directory, so the bash
  agents (`gh issue list`, `cat docs/PRD.md`, `git checkout`) operate on
  the right repo.

**After import:** open the pipeline in the Designer, optionally trigger a
dry test on individual agents (each has a Test panel — see #103), then
run it. Each run leaves node-by-node logs at `/runs/<id>` so you can iterate
on prompts.

### `team-collaboration.pipeline-bundle.json`

Three-node example demonstrating the v0.3 multi-user import flow.
Takes a free-form problem statement (via the `description` field on
`PipelineState`) and produces a GitHub-issue-ready hand-off brief
for a teammate (in `implementation_notes`).

```
__start__ → extract_scope → draft_brief → format_issue → __end__
```

| Node | Runtime | Role | What it does |
|---|---|---|---|
| `extract_scope` | api-call (glm) | task_selector | Reads `description`, writes structured outline (title + scope + constraints) into `implementation_notes` |
| `draft_brief` | api-call (glm) | writer | Reads `implementation_notes`, appends acceptance criteria + approach + risks |
| `format_issue` | api-call (glm) | writer | Reads `implementation_notes`, rewrites as GitHub issue body |

**State-field contract:** every agent's `input_schema` /
`output_schema` uses only valid `PipelineState` fields
(`description`, `implementation_notes`) — bundle import refuses
agent schemas referencing unknown fields. The downstream brief
content accumulates in `implementation_notes` (a `str | None`
field) across the three nodes; the operator reads the final
markdown out of it at the end of the run.

**Why this bundle exists:** The bundle has **no `user_id` field
anywhere** — that's the v0.3 multi-user import contract. When you
import a bundle, the engine assigns ownership of the pipeline AND
every bundled agent to the importing user. Two teammates can each
import the same JSON and end up with independent copies; either
can edit theirs without affecting the other's. Use this as a
template for the bundles you'll share within your team.

**Prerequisites:**

- `GLM_API_KEY` exported in the engine's env (or edit the JSON to
  swap providers).
- An initial state with `description: "..."` (and the standard
  `run_id` / `repo` / `branch`) when triggering the run. Use the
  dashboard's run-trigger form or `POST /runs` with an
  `initial_state` object.

**After running:** the final markdown lands in
`state.implementation_notes`. Extract the first H1 line as the
issue title, the rest as the body, and pipe:

```bash
TITLE="<copy from H1>"
gh issue create --title "$TITLE" --body-file <(echo "$BODY")
```

### `ops-pipeline`

Four-node pipeline for **command-execution issues** — issues that require
running shell commands on a remote host (benchmarks, migrations, service
restarts) instead of producing a code change.

```
__start__ → trigger → specify → executor → done → __end__
```

| Node | Role | What it does |
|---|---|---|
| `trigger` | `post_check` | Validates `extensions.execution_target` is present |
| `specify` | `task_selector` | Validates commands list and produces a human-readable run-plan |
| `executor` | `post_check` | Runs commands on the target host via the Cortex executor node (Dixter999/cortex-project#446) |
| `done` | `post_check` | Posts results back to the GitHub issue via `gh issue comment` |

**Extension keys** (passed in `initial_state.extensions` at trigger time):

| Key | Type | Required |
|---|---|---|
| `execution_target` | `string` | yes |
| `execution_commands` | `list[string]` | yes |
| `execution_env` | `dict[string, string]` | no — pass `GITHUB_ISSUE_URL` here for automatic write-back |

**Note:** This bundle lives in its own subdirectory (`examples/ops-pipeline/`)
rather than the flat `examples/pipelines/` convention used by the other bundles.
See [`examples/ops-pipeline/README.md`](../ops-pipeline/README.md) for the full
node table, trigger payload example, GitHub write-back ownership statement, and
Cortex executor prerequisites.
