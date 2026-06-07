# Example: a minimal `python-func` pipeline

A runnable, tested example of a pipeline whose nodes run your own Python — the
same two-layer pattern Cortex uses. Full walkthrough:
[`docs/python-func-pipelines.md`](../../docs/python-func-pipelines.md).

```
examplepipe/
  nodes.py   greet(state, config) -> {"extensions": {"greeting": ...}}
             shout(state, config) -> uppercases the greeting
  gates.py   noop(state, config)  -> {}  (human-approval gate target)
example.pipeline-bundle.json   greet -> shout -> gate -> end
```

The bundle holds **no code** — only the wiring. Each agent points at a callable
by `module:function` (e.g. `examplepipe.nodes:greet`); DAP imports it at runtime.

## Try it

```bash
# Prove the nodes run and the bundle's callable paths resolve:
uv run pytest tests/smoke/test_example_python_func_pipeline.py -q

# To run it inside DAP, install this package in the engine venv first, then
# import the bundle (or rebuild the graph in the Designer):
uv pip install -e examples/python-func-pipeline
```
