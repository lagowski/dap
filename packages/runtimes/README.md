# dap-runtimes

Runtime adapters dla DAP. Każdy adapter deleguje zadanie do konkretnego executora (CLI agent, SDK, shell, HTTP).

Stan: `api-call` (multi-provider: Anthropic + OpenAI + OpenAI-compat + Gemini), `bash`, `claude-code`, `codex`, `gemini-cli` i `http` są w pełni zaimplementowane; `aider` — stub (przyjdzie później).

```python
import asyncio
from dap_runtimes import create_default_registry

async def main() -> None:
    registry = create_default_registry()
    adapter = registry.get("bash")
    health = await adapter.healthcheck()
    print(health)

asyncio.run(main())
```

## api-call

Wywołanie LLM przez SDK — single-shot, bez tool use, bez streamowania.
Provider wybierany przez `runtime_config.provider`; każdy provider ma
własne pricing + token extraction w module pod `_providers/`.

| `provider`        | SDK                                | Wymagany env                                    | Pricing                                                  |
| ----------------- | ---------------------------------- | ----------------------------------------------- | -------------------------------------------------------- |
| `anthropic` (default) | `anthropic.AsyncAnthropic`     | `ANTHROPIC_API_KEY`                             | Pełne (Claude 4.x rodzina + cache mnożniki)              |
| `openai`          | `openai.AsyncOpenAI`               | `OPENAI_API_KEY`                                | Pełne (gpt-5, o-series)                                  |
| `openai-compat`   | `openai.AsyncOpenAI(base_url=…)`   | env nazwany w `runtime_config.api_key_env`      | None (nie znamy cen third-party)                         |
| `gemini`          | `google.genai.Client`              | `GEMINI_API_KEY`                                | Pełne (Gemini 2.x / 3.x)                                 |

`runtime_config` (wspólne):

| key                | type           | description                                                                  |
| ------------------ | -------------- | ---------------------------------------------------------------------------- |
| `provider`         | `str`          | `anthropic` (domyślnie) / `openai` / `openai-compat` / `gemini`              |
| `model_id`         | `str`          | ID modelu (np. `claude-haiku-4-5`, `gpt-5-mini`, `gemini-3.0-flash`)          |
| `max_tokens`       | `int`          | Domyślnie 4096                                                               |
| `system_prompt`    | `str?`         | Prepend przed `prompt_xml` w roli system                                      |

Specyficzne dla `anthropic`:

| key               | type                                                | description                              |
| ----------------- | --------------------------------------------------- | ---------------------------------------- |
| `prompt_cache`    | `bool`                                              | Włącza ephemeral cache control           |
| `enable_thinking` | `bool`                                              | Adaptive thinking                        |
| `effort`          | `low`/`medium`/`high`/`xhigh`/`max`                 | Reasoning effort                          |

Specyficzne dla `openai-compat`:

| key            | type   | required | description                                                          |
| -------------- | ------ | :------: | -------------------------------------------------------------------- |
| `base_url`     | `str`  |   tak    | OpenAI-compat endpoint (np. `https://api.z.ai/api/coding/paas/v4`)   |
| `api_key_env`  | `str`  |   tak    | Nazwa env var trzymającej klucz (np. `GLM_API_KEY`)                  |

Specyficzne dla `gemini`:

| key                | type   | description                                       |
| ------------------ | ------ | ------------------------------------------------- |
| `temperature`      | `float`| Domyślnie SDK default                             |
| `thinking_budget`  | `int`  | Limit tokenów na reasoning                        |

### Przykłady

**DeveloperJr (GLM przez OpenAI-compat):**

```json
{
  "provider": "openai-compat",
  "model_id": "glm-5-flash",
  "base_url": "https://api.z.ai/api/coding/paas/v4",
  "api_key_env": "GLM_API_KEY",
  "max_tokens": 4096
}
```

**DeveloperFrontend (Gemini API):**

```json
{
  "provider": "gemini",
  "model_id": "gemini-3.0-pro",
  "max_tokens": 8192,
  "temperature": 0.2
}
```

## bash

Wykonuje pojedynczą komendę shellową w `task.working_directory`, z timeoutem
z `task.timeout_ms`. Zwraca `success=False` przy non-zero exit / timeout;
stdout trafia do `output`, stderr i exit_code do `structured`.

`runtime_config`:

| key       | type             | required | description                                                       |
| --------- | ---------------- | :------: | ----------------------------------------------------------------- |
| `command` | `str`            |    no    | Komenda do uruchomienia. Pierwszeństwo nad `<command>` w prompcie. |
| `shell`   | `str`            |    no    | Domyślnie `/bin/bash`. Override też przez `DAP_BASH_SHELL`.        |
| `env`     | `dict[str, str]` |    no    | Dodatkowe zmienne środowiskowe (zlewane na proces.env).           |

Jeśli `command` nie jest ustawione, adapter wyciąga zawartość pierwszego
`<command>...</command>` z `prompt_xml` — pozwala to na komendy wyliczane
z `PipelineState` przez Jinja w template'cie agenta.

### Model bezpieczeństwa (v0.1)

**Single-user, local-trust.** Komenda działa z uprawnieniami procesu engine'a
w skonfigurowanym `working_directory` — **bez sandboxa, bez izolacji sieci,
bez confinementu plików**. Każda definicja agenta używającego runtime'u `bash`
ma faktyczną kontrolę nad maszyną. Multi-user setup wymaga najpierw auth
(#38) i dodatkowej warstwy izolacji (firejail / Docker / nsjail) zanim ten
runtime można wystawić niezaufanym definicjom agentów.

## Env layering (v0.6)

Subprocess-spawning adapters (`bash`, `claude-code`, `codex`, `gemini-cli`)
budują env subprocesa w trzech warstwach. Wyższa warstwa wygrywa nad niższą:

1. **Engine process env** (najniższa) — env, w którym działa `dap-engine`.
   Trzymaj tu sekrety (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`).
2. **Project `env_vars`** (overlay) — `Project.env_vars` projektu związanego z
   runem (#65). Wartości wyższe od engine env, niższe od per-agent. Convenience
   dla nie-sekretnych zmiennych typu `WORKSPACE_NAME`, feature flagów, ścieżek
   per-projekt.
3. **Per-agent `runtime_config.env`** (najwyższa) — wprost w agent definition.
   Ostatnia szansa na override per-call.

Ad-hoc runs (bez `project_id`) widzą tylko warstwy 1 + 3 — warstwa 2 jest pustym
dictem. `api-call` adapter nie spawnuje subprocesów, więc env layering go nie
dotyczy (klucz API czyta SDK z `os.environ` bezpośrednio).

## claude-code

Wywołuje Claude Code CLI w trybie `--print --output-format json`. Prompt
XML idzie przez stdin, odpowiedź to structured JSON (`result`, `usage`,
`total_cost_usd`, `session_id`, `num_turns`). Cost i token usage
populują `RuntimeResult` automatycznie.

Kiedy `claude-code` zamiast `api-call` (Anthropic):

| Potrzeba                                              | Użyj                |
| ----------------------------------------------------- | ------------------- |
| Single-shot LLM, deterministycznie, bez tooli         | `api-call`          |
| Agentic loop z file edits, bashem, MCP tools          | `claude-code`       |

`runtime_config`:

| key            | type        | required | description                                                              |
| -------------- | ----------- | :------: | ------------------------------------------------------------------------ |
| `model_id`     | `str`       |    tak   | Anthropic model (np. `claude-opus-4-7`, `claude-sonnet-4-6`)              |
| `binary_path`  | `str`       |    no    | Domyślnie `claude` na PATH. Override przy wielu instalacjach.            |
| `extra_args`   | `list[str]` |    no    | Dopisane do CLI invocation, np. `["--allowed-tools","Read,Edit,Bash"]`  |

Klucz API: `ANTHROPIC_API_KEY` w env engine'a — Claude Code CLI czyta go
sam, adapter nie wstrzykuje.

Process lifecycle identyczny jak w `bash`: POSIX session group +
`asyncio.shield(wait)` cleanup + obsługa `CancelledError`. Engine
pause/abort zabija całą grupę procesów (CLI + jego subprocessy), bez
zombie.

## codex

Wywołuje OpenAI Codex CLI w trybie `exec --json --model {model_id}`.
Prompt XML idzie przez stdin, wynik to structured JSON. Token usage
wyciągamy z `usage` z fallbackami nazw (snake_case z OpenAI SDK,
camelCase z niektórych buildów, oraz wariant `prompt_tokens` /
`completion_tokens`). Tekst odpowiedzi szukamy kolejno w
`output_text` → `result` → `response` → `text` → `output`. Cost
zwracany jako `None` (CLI nie raportuje; jeśli chcesz pricing — użyj
`api-call` z `provider="openai"`).

Kiedy `codex` zamiast `api-call` (OpenAI):

| Potrzeba                                              | Użyj                |
| ----------------------------------------------------- | ------------------- |
| Single-shot LLM, deterministycznie, bez tooli         | `api-call`          |
| Agentic loop z file edits, bashem, MCP tools          | `codex`             |

`runtime_config`:

| key            | type        | required | description                                                              |
| -------------- | ----------- | :------: | ------------------------------------------------------------------------ |
| `model_id`     | `str`       |    tak   | OpenAI model (np. `gpt-5-codex`, `gpt-5`, `o3`)                          |
| `binary_path`  | `str`       |    no    | Domyślnie `codex` na PATH. Override przy wielu instalacjach.            |
| `extra_args`   | `list[str]` |    no    | Dopisane do CLI invocation, np. `["--sandbox","workspace-write"]`       |

Klucz API: `OPENAI_API_KEY` w env engine'a — Codex CLI czyta go sam,
adapter nie wstrzykuje.

Process lifecycle identyczny jak w `bash` / `claude-code`. Cały payload
JSON z CLI ląduje w `RuntimeResult.structured.payload` — pozwala
debugować shape drift między releasami CLI bez zmian adaptera.

## gemini-cli

Wywołuje Google Gemini CLI z `-m {model_id} -o json`, prompt XML idzie
przez stdin, wynik to structured JSON. Token usage wyciągamy z
`usage_metadata` (snake_case z SDK lub camelCase z niektórych buildów —
adapter próbuje obu). Cost zwracany jako `None` (CLI nie raportuje;
jeśli chcesz pricing — użyj `api-call` z `provider="gemini"`).

`runtime_config`:

| key                | type   | required | description                                                              |
| ------------------ | ------ | :------: | ------------------------------------------------------------------------ |
| `model_id`         | `str`  |    tak   | Gemini model (np. `gemini-3.0-pro`, `gemini-3.0-flash`)                   |
| `binary_path`      | `str`  |    no    | Domyślnie `gemini` na PATH                                                |
| `thinking_budget`  | `int`  |    no    | Limit tokenów na reasoning (mappowany do `--thinking-budget`)             |

Klucz API: `GEMINI_API_KEY` w env engine'a — albo `GOOGLE_API_KEY` jako
fallback (Gemini CLI akceptuje oba). Adapter nie wstrzykuje, CLI czyta
sam.

Process lifecycle identyczny jak w `bash` / `claude-code`.

## http

Generic POST do dowolnego JSON-speaking serwisu — Ollama, llama.cpp,
custom gatewaye, OpenAI/Anthropic-compatible proxy, własne API teamu.
Request body to Jinja2 template (sandboxed) renderowany ze scope'm
`{prompt_xml, runtime_config}`; response parsowany przez JSONPath.

`runtime_config`:

| key                  | type            | required | description                                                                  |
| -------------------- | --------------- | :------: | ---------------------------------------------------------------------------- |
| `url`                | `str`           |    tak   | Endpoint (http:// lub https://)                                              |
| `method`             | `str`           |    no    | `POST` (default) lub `PUT`                                                    |
| `request_template`   | `dict`/`list`/`str` | tak | Template z Jinja2 placeholders. Stringi templatowane, inne typy as-is.       |
| `response_extractor` | `dict[str, str]` |   tak   | Mapa nazw → JSONPath. **Musi zawierać `output`**. Np. `{"output":"$.response"}` |
| `auth`               | `dict`          |    no    | `{"type":"bearer","env":"OLLAMA_API_KEY"}` / `{"type":"header","name":"...","env":"..."}` / `{"type":"basic","user_env":"...","pass_env":"..."}` |
| `headers`            | `dict[str,str]` |    no    | Dodatkowe statyczne headery                                                  |

Klucze API: zawsze przez env vary nazwane w `auth.env` / `auth.user_env`
/ `auth.pass_env` — nigdy nie persystowane w bazie.

Przykład Ollama:

```json
{
  "url": "http://localhost:11434/api/generate",
  "request_template": {
    "model": "{{ runtime_config.model_id }}",
    "prompt": "{{ prompt_xml }}",
    "stream": false
  },
  "response_extractor": {
    "output": "$.response",
    "tokens_used": "$.eval_count"
  },
  "model_id": "llama3.2"
}
```

Cancellation: httpx connection cancel'owane przez context manager
(`async with`); engine pause/abort kończy request bez residue.

## python-func

Wykonuje dowolny Python callable (sync lub async) jako agenta DAP.
Entrypoint rozwiązywany w momencie wywołania — instalacja pakietu
nie wymaga restartu engine'a.

### Sygnatura funkcji

```python
async def run(state: dict, config: dict) -> dict:
    """
    state  — dict budowany z pól przełączanych przez pass_prompt / pass_context.
    config — runtime_config z definicji agenta.
    returns — dict pól do scalenia z PipelineState.
              Zarezerwowany klucz __audit: dict jest wyciągany przez adapter
              i trafia do RuntimeResult.structured["audit"] (NIE do state_delta).
    """
    return {
        "output_field": result_text,
        "__audit": {"tokens_used": 1240, "cost_usd": 0.003},
    }
```

Sync `def run(state, config)` też działa — adapter owija w `loop.run_in_executor(None, ...)`.

`runtime_config`:

| key             | type   | required | description                                                                         |
| --------------- | ------ | :------: | ----------------------------------------------------------------------------------- |
| `callable_path` | `str`  |    tak   | `"package.module:func_name"`. Pakiet musi być zainstalowany w venv engine'a.        |
| `pass_prompt`   | `bool` |    no    | Czy wstrzyknąć `prompt_xml` do `state["prompt_xml"]`. Domyślnie `True`.             |
| `pass_context`  | `bool` |    no    | Czy wstrzyknąć `RuntimeContext` do `state["context"]`. Domyślnie `False`.           |

`RuntimeResult.structured`:

| key           | type   | description                                                          |
| ------------- | ------ | -------------------------------------------------------------------- |
| `state_delta` | `dict` | Zwrot funkcji po usunięciu `__audit` — gotowy do scalenia ze statem. |
| `audit`       | `dict` | Zawartość `__audit` z returna (tokeny, koszt, custom pola).          |

### Przykład

```json
{
  "runtime_id": "python-func",
  "runtime_config": {
    "callable_path": "cortex.nodes.mockup:run",
    "pass_prompt": true
  }
}
```

### Model bezpieczeństwa (v0.1)

**Single-user, local-trust.** Ten sam poziom zaufania co `bash` — callable
działa z uprawnieniami procesu engine'a, **bez sandboxa, bez izolacji importów,
bez confinementu sieci**. Pakiet musi być zainstalowany w venv engine'a.
Multi-user setup wymaga najpierw auth (#38) i dodatkowej warstwy izolacji
zanim ten runtime można wystawić niezaufanym definicjom agentów.
