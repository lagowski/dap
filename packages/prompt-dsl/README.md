# dap-prompt-dsl

Prompt Builder — kompilator JSON context + Jinja2 template → walidowany XML prompt dla DAP agentów.

## Why

DAP zasada: **prompt jest kodem**. Każdy agent ma `prompt_template` (Jinja2) i `input_schema`. Builder jest **deterministyczną pure function** — input → output, brak side effects, brak LLM. Sandbox + defused parser dla bezpieczeństwa.

## Usage

```python
from dap_prompt_dsl import build_prompt

result = build_prompt(
    template="<agent_prompt><role>{{ role }}</role></agent_prompt>",
    context={"role": "test_author"},
)
# BuildResult(xml="...", warnings=[], valid=True)
```

## Bezpieczeństwo

- **Jinja2 SandboxedEnvironment** — agent prompt_template może pochodzić z user input (dashboard); sandbox blokuje access do dunder methods, file I/O.
- **defusedxml** dla parsowania — protections XXE, billion laughs, external entities.
