# dap-runtimes

Runtime adapters dla DAP. Każdy adapter deleguje zadanie do konkretnego executora (CLI agent, SDK, shell, HTTP).

Stan: `api-call` i `bash` są w pełni zaimplementowane; `http`, `claude_code`, `gemini_cli`, `codex`, `aider` — stuby (przyjdą później).

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
