# dap-runtimes

Runtime adapters dla DAP. Każdy adapter deleguje zadanie do konkretnego executora (CLI agent, SDK, shell, HTTP).

Stan: `api-call` (multi-provider: Anthropic + OpenAI + OpenAI-compat + Gemini), `bash` i `claude-code` są w pełni zaimplementowane; `http`, `gemini-cli`, `codex`, `aider` — stuby (przyjdą później).

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
