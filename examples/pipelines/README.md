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
