# dap-runtimes

Runtime adapters dla DAP. Każdy adapter deleguje zadanie do konkretnego executora (CLI agent, SDK, shell, HTTP).

W F0 wszystkie adaptery są stubami — implementacja w F3 (1st wave: bash, http, api_call, claude_code) i F9 (gemini_cli, codex, aider).

```python
from dap_runtimes import create_default_registry

registry = await create_default_registry()
adapter = registry.get("bash")
health = await adapter.healthcheck()
```
