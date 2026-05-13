# dap-prompt-dsl

Deterministic prompt compiler for the
[DAP](https://github.com/rafeekpro/dap) (Deterministic Agent Pipeline)
ecosystem. Takes a Jinja2 template plus a JSON context and emits a
validated XML prompt — the wire format every DAP agent uses to talk
to its runtime adapter.

## Why this exists

DAP's core principle is **"the prompt is code"**: every agent's
behaviour is defined by a versioned `prompt_template` and a
declared `input_schema` against `PipelineState`. To keep that
principle intact, the prompt compiler must be:

- **Pure** — same inputs always produce the same output. No
  hidden randomness, no time-dependent rendering, no network
  calls during render. The result is reproducible across machines
  and across time.
- **Sandboxed** — agent templates come from end-user input via
  the dashboard. The compiler must not let a template author
  read the host filesystem or escape into the engine process.
- **Strict on shape** — the rendered output is XML the runtime
  parses. Malformed or unsafe XML is rejected at compile time,
  not at the runtime adapter, so the failure mode is clean.

## What's in here

| Function / class | Purpose |
|---|---|
| `build_prompt(template, context)` | Single entry point. Renders the Jinja2 template under the supplied context, parses the result as XML, returns a `BuildResult`. |
| `BuildResult` | `(xml: str, valid: bool, warnings: list[str], errors: list[str])` — `valid` is False when XML parsing or schema validation fails; details land in `errors`. |
| `SandboxedEnvironment` (subclass) | Jinja2 sandbox configured with DAP-specific filters (`tojson`, `pluralize`, ...) and default-deny on dunder access, attribute traversal into Python builtins, etc. |
| `defused_parser` | `defusedxml.ElementTree`-based parser that rejects XXE, billion-laughs, external entities, and DTDs. |

## Installation

Usually installed transitively via `dap-cli`:

```bash
pipx install dap-cli
```

Standalone (if you're embedding the prompt compiler in another
tool — e.g. a linter that validates agent templates without
running the engine):

```bash
pip install dap-prompt-dsl
```

## Usage

```python
from dap_prompt_dsl import build_prompt

result = build_prompt(
    template=(
        "<agent_prompt version=\"1\">"
        "<role>{{ role }}</role>"
        "<task>{{ task }}</task>"
        "</agent_prompt>"
    ),
    context={"role": "test_author", "task": "Write a unit test for issue #42."},
)

if not result.valid:
    raise ValueError(result.errors)

print(result.xml)
# <agent_prompt version="1"><role>test_author</role><task>Write a unit test for issue #42.</task></agent_prompt>
```

The engine calls `build_prompt` once per node execution as part
of the LangGraph state machine. The same call also drives the
dashboard's **Render preview** button on `/agents/<id>/edit`, so
template authors can see the compiled XML without burning LLM
tokens.

## Security model

This package handles untrusted input — agent templates can be
authored by any operator with `is_active=True` on the engine.
Two layers of defense:

- **Jinja2 `SandboxedEnvironment`** — blocks access to dunder
  methods (`__class__`, `__mro__`, ...), prevents traversal into
  Python builtins, refuses `getattr` on objects not registered as
  template-safe. Templates can render strings, numbers, lists,
  and dicts from the context; they can't open files, spawn
  subprocesses, or import modules.
- **`defusedxml`** — XML parsing rejects XXE attacks (external
  entity injection), billion-laughs (entity expansion DoS), and
  DTD inclusion. The parser is strict-byte mode; embedded null
  bytes are rejected before parsing begins.

A successful `build_prompt` call means the XML is safe to log,
safe to send to the runtime adapter, and free of injection
vectors that the runtime might mis-parse. Adapters still validate
the agent_prompt envelope themselves before handing off to the
LLM — defence in depth.

## Compatibility

- Python 3.13+
- Jinja2 3.1+, defusedxml 0.7+

## See also

- [DAP project README](https://github.com/rafeekpro/dap) — full architecture.
- [`docs/architecture.md`](https://github.com/rafeekpro/dap/blob/main/docs/architecture.md) — where `build_prompt` sits in the LangGraph state machine.
- [`packages/runtimes`](https://github.com/rafeekpro/dap/tree/main/packages/runtimes) — consumers of the compiled prompt XML.

## License

See the [main repository](https://github.com/rafeekpro/dap) for licensing details.
