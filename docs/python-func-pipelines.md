# Building a `python-func` pipeline (the Cortex pattern)

How to build a pipeline whose nodes run **your own Python code** — the same
two-layer design Cortex uses. A runnable, tested example lives in
[`examples/python-func-pipeline/`](../examples/python-func-pipeline/).

## The two layers

A `python-func` pipeline is **Python functions wired by a JSON graph** — not
pure Python, and not pure config:

| Layer | What it is | Where it lives |
| --- | --- | --- |
| **1 — Logic** | `async def fn(state, config) -> dict` node functions | a **Python package** installed in the engine's venv |
| **2 — Wiring** | nodes, edges, conditions, gates, which callable each node runs | a **bundle JSON** DAP imports into its DB |

The bundle JSON contains **no code** — only a manifest. Each agent entry says
"this node runs the function at `mypkg.nodes:greet`, timeout 5s." At runtime DAP
does, literally:

```python
from mypkg.nodes import greet
await greet(state, config)
```

So the JSON is a **pointer**; the package is the **behavior**. That's why the
exported JSON "looks empty" — the code was never in it.

## The node contract

```python
async def greet(state: dict, config: dict) -> dict:
    # state  — the live PipelineState. Put non-engine-native fields under
    #          state["extensions"] so edge conditions can read extensions.<key>.
    # config — this node's runtime_config extras (one function, many nodes).
    # return — a dict of state updates the engine merges back.
    name = (state.get("extensions") or {}).get("name", "world")
    return {"extensions": {"greeting": f"Hello, {name}!"}}
```

Keep nodes pure where possible (no external writes) so the engine can retry them
idempotently; push side effects into their own downstream node. **Gates are
no-ops** — the pause is configured at the graph level (`approval_required_nodes`),
never by calling pause from inside the node.

## How the import works

DAP imports `mypkg` because the package is on the engine venv's `sys.path`. With
an editable install that's a single `.pth` file in `site-packages` pointing at
your project directory — exactly how Cortex is wired:

```
/home/.../dap/.venv/lib/python3.13/site-packages/cortex-project.pth
  → /home/.../Projects/cortex-project        # adds this dir to sys.path
```

## Build your own — step by step

1. **Write a package** of node functions (see the example's `examplepipe/`).

2. **Install it editable in the engine's venv** — this is the one step that
   needs filesystem access on the engine host (it can't be done from the
   dashboard):

   ```bash
   cd /path/to/dap            # the engine project (its venv is the target)
   uv pip install -e /path/to/mypkg
   # verify the engine can import it:
   uv run python -c "from mypkg.nodes import greet; print(greet)"
   ```

3. **Create agents in DAP** (Agents → New) — one per callable:
   - Runtime = `python-func`
   - Runtime config = `{ "callable_path": "mypkg.nodes:greet" }` (+ any extra
     keys you want passed to the node as `config`)
   - `prompt_template` is **required by the schema but ignored** for
     `python-func` — it's an inert placeholder (the node builds its own prompt
     in code, if it calls an LLM at all).

4. **Wire the graph** in the Designer (Pipelines → New → blank canvas): drop the
   agents as nodes, draw edges, add conditions (`extensions.<field>` predicates)
   and approval gates. **Export** to get a portable bundle JSON.

5. **Run** — DAP imports each `callable_path` and calls `await fn(state, config)`,
   following edges and pausing before any `approval_required_nodes`.

> The reliable way to get a valid bundle is to **draw it in the Designer and
> Export**, rather than hand-maintaining the JSON.

## ⚠️ Keep the package's deps installed across engine deploys

Your package's **dependencies** (e.g. `pydantic-settings`, `pygithub` for
cortex) live in the engine venv but are **not** in DAP's lockfile. So
`uv sync --all-packages` — which a routine engine deploy runs — **removes
them**, silently breaking every `python-func` node with `ModuleNotFoundError`
at the next run even though nothing in your package changed. (Cortex regressed
exactly this way on 2026-06-06.)

Deploy the engine with [`scripts/deploy-engine.sh`](../scripts/deploy-engine.sh)
instead of a bare `uv sync` — it runs the sync, then reinstalls the out-of-lock
deps (`CORTEX_EXTRA_DEPS`), verifies the package still imports, and restarts the
engine. The pre-run readiness check (shown in the run dialog) also surfaces an
unresolvable callable before you trigger a run, so a wiped dep shows up as a
clear "node X can't import …" rather than a cryptic mid-run failure.

## Why you can't author the node code from the DAP UI

DAP orchestrates and runs your callables, but it does **not** edit or install
their code — that's a Python package on the engine host (a normal repo + an
editable install), just like `cortex-project`. The dashboard owns the *wiring*;
the package owns the *logic*. To change what a node does, you edit the package
and `git pull` + restart the engine — not the DAP UI.

## See it run

The example is covered by `tests/smoke/test_example_python_func_pipeline.py`,
which imports and executes the nodes and checks every bundle `callable_path`
resolves — the same `module:function` import DAP performs:

```bash
uv run pytest tests/smoke/test_example_python_func_pipeline.py -q
```
