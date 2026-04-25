# DAP — Deterministic Agent Pipeline

[![CI](https://github.com/rafeekpro/dap/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/rafeekpro/dap/actions/workflows/ci.yml)

Lokalna aplikacja do budowy i wykonywania deterministycznych pipeline'ów agentowych. Paperclip-like UX (`uvx dap`), DAP-owe zasady (state machine + XML prompts + runtime adapters).

Pełny plan architektoniczny: [`../LOCAL_APP_PLAN.md`](../LOCAL_APP_PLAN.md)
Dokumentacja koncepcyjna: [`../DOCUMENTATION.md`](../DOCUMENTATION.md)
Workflow: [`CONTRIBUTING.md`](CONTRIBUTING.md)

---

## Stan implementacji

### ✅ F0 — Monorepo skeleton (Python + uv, ukończone 2026-04-25)

Pivot z TS/Node na Python 3.13 + uv workspace. Scaffolding wszystkich workspace'ów, FastAPI + SQLAlchemy + SQLite engine, Typer CLI, 7 runtime adapter stubs.

**Zweryfikowane end-to-end:**
- `uv sync --all-packages` — workspace z 4 pakietami zbudowany
- `uv run dap --version` → `0.0.1`
- `dap init` → tworzy `.dap/` z pełną strukturą
- `dap start` → engine na `127.0.0.1:7333` (uvicorn + FastAPI)
- `GET /health` → `{"status":"ok","service":"dap-engine","version":"0.0.1"}`
- `GET /runtimes` → 7 adapterów (bash, http, api-call, claude-code, gemini-cli, codex, aider)
- `GET /runtimes/:id/health` → healthcheck per runtime
- SQLite `state.db` + WAL mode (`state.db-shm`, `state.db-wal`)
- `ruff check + format` — clean
- Wszystkie 5 tabel (agents, pipelines, runs, state_snapshots, node_execution_logs) tworzone automatycznie przy starcie

### Planowane

| Faza  | Zakres                                                                  |
| ----- | ----------------------------------------------------------------------- |
| F1    | CLI process management (PID file, `dap stop`, `dap status` runtime info) |
| F2    | Engine: REST CRUD dla agents/pipelines/runs (FastAPI + Pydantic)        |
| F3    | Runtime adapters 1st wave — `api-call`, `bash`, `claude-code`           |
| F4    | Prompt Builder (Jinja2 → XML, schema validator)                          |
| F5    | Pipeline execution (LangGraph integration, state diff, checkpointing)   |
| F6    | Dashboard viewer (Next.js, Runs List, Run Detail, Node Drawer)          |
| F7    | Dashboard Pipeline Designer (React Flow + designer)                     |
| F8    | Agent & runtime registry UI                                             |
| F9    | Runtime adapters 2nd wave — `gemini-cli`, `codex`, `aider`, `http`      |
| F10   | New Run wizard + GitHub PAT integration                                 |
| F11   | Polish: keychain (keyring lib), export/import, dark mode                |
| F12   | Packaging, `uv tool install`, cross-platform verification               |

---

## Struktura monorepo

```
dap/
├─ pyproject.toml              root + uv workspace (members)
├─ uv.lock
├─ .python-version             3.13
├─ .gitignore, README.md, CONTRIBUTING.md
├─ .github/, .githooks/
│
├─ apps/
│  ├─ cli/                     dap-cli — Typer launcher
│  │  └─ src/dap_cli/
│  │     ├─ __main__.py        Typer app (dap init|start|stop|status|--version)
│  │     ├─ paths.py           .dap/, config.json, state.db
│  │     └─ commands/          init, start, stop, status
│  │
│  ├─ engine/                  dap-engine — FastAPI + LangGraph + SQLAlchemy
│  │  └─ src/dap_engine/
│  │     ├─ app.py             create_app() factory + lifespan
│  │     ├─ __main__.py        standalone entry (uv run dap-engine)
│  │     ├─ api/               health, runtimes
│  │     └─ persistence/
│  │        ├─ models.py       SQLAlchemy 2.0 ORM (5 tabel)
│  │        └─ db.py           SQLite + WAL mode + session factory
│  │
│  └─ dashboard/                Next.js — placeholder (F6)
│
└─ packages/
   ├─ types/                    dap-types — Pydantic v2 models
   │  └─ src/dap_types/
   │     ├─ agent.py, pipeline.py, run.py, state.py, runtime.py
   │
   └─ runtimes/                 dap-runtimes — RuntimeAdapter Protocol + impls
      └─ src/dap_runtimes/
         ├─ registry.py         RuntimeRegistry + create_default_registry
         └─ adapters/           base, bash, http, api_call,
                                claude_code, gemini_cli, codex, aider
```

## Tech stack

| Warstwa               | Wybór                                  |
| --------------------- | -------------------------------------- |
| Runtime               | Python 3.13                            |
| Package manager       | uv 0.8                                 |
| Monorepo              | uv workspaces                          |
| CLI                   | Typer 0.12 + Rich                      |
| Engine HTTP           | FastAPI 0.115 + uvicorn                |
| State machine         | LangGraph (Python, F5+)                |
| ORM                   | SQLAlchemy 2.0 + Alembic (migrations)  |
| DB                    | SQLite (WAL mode)                      |
| Schema validation     | Pydantic v2                            |
| Subprocess            | anyio + asyncio.subprocess             |
| HTTP client           | httpx                                  |
| Lint/format           | ruff                                   |
| Type checker          | mypy strict                            |
| Tests                 | pytest + pytest-asyncio                |
| Dashboard (F6)        | Next.js 15 + shadcn/ui + React Flow    |

---

## Quickstart

Wymagania: Python 3.12+ i [uv](https://docs.astral.sh/uv/).

```bash
cd /Users/rla/RLA02/PROJEKTY/Developer/dap

uv sync --all-packages       # zainstaluj cały workspace + deps

# Test CLI
uv run dap --version          # → 0.0.1
uv run dap --help

# Test pełnego flow
mkdir -p /tmp/dap-playground && cd /tmp/dap-playground
$OLDPWD/.venv/bin/dap init
$OLDPWD/.venv/bin/dap start
# Ctrl+C żeby zatrzymać

# Po F12 (packaging): uv tool install dap-cli → `dap init`
```

## Dostępne komendy CLI

| Komenda          | Status      | Opis                                                                    |
| ---------------- | ----------- | ----------------------------------------------------------------------- |
| `dap --version`  | ✅          | Wersja binarki                                                          |
| `dap --help`     | ✅          | Lista komend                                                            |
| `dap init`       | ✅          | Tworzy `./.dap/` z config.json i podkatalogami                          |
| `dap start`      | ✅          | Spawn engine na 127.0.0.1:7333; PID file `./.dap/dap.pid`; refuse jeśli już działa; cleanup stale PID |
| `dap stop`       | ✅          | SIGTERM → wait 5s → SIGKILL fallback; cleanup PID file                  |
| `dap status`     | ✅          | Pokazuje stan engine (running/stopped/stale), PID + port + uptime, tabelę runtime adapterów z healthcheck |

## Dostępne endpointy engine

### Health + runtimes

```bash
curl http://127.0.0.1:7333/health
# → {"status":"ok","service":"dap-engine","version":"0.0.1","timestamp":"..."}

curl http://127.0.0.1:7333/runtimes                       # lista 7 adapterów
curl http://127.0.0.1:7333/runtimes/bash/health           # {"available":true,...}
curl http://127.0.0.1:7333/runtimes/claude-code/health    # {"available":false,...}
```

### Agents (F2 — pełen CRUD z immutable versioning)

```bash
POST   /agents                              # create v1
GET    /agents?role=test_author&limit=50    # list (filters: role, archived, offset, limit)
GET    /agents/{id}                         # current version
PUT    /agents/{id}                         # create new version (vN+1)
DELETE /agents/{id}                         # archive (soft delete)
GET    /agents/{id}/versions                # full history
GET    /agents/{id}/versions/{v}            # specific version
```

### Pipelines (F2 — analogicznie)

```bash
POST   /pipelines
GET    /pipelines?archived=false
GET    /pipelines/{id}
PUT    /pipelines/{id}
DELETE /pipelines/{id}
GET    /pipelines/{id}/versions
GET    /pipelines/{id}/versions/{v}
```

### Runs (F2 — read-only; lifecycle ops w F5+)

```bash
GET /runs?pipeline_id=...&final_status=...
GET /runs/{id}
GET /runs/{id}/state              # latest snapshot
GET /runs/{id}/state/history      # all snapshots
GET /runs/{id}/nodes/{node_id}    # execution log
```

Endpointy do triggerа / pause / resume / abort / retry / skip wymagają silnika LangGraph — w planie F5+.

## Baza danych

SQLAlchemy 2.0 ORM w `apps/engine/src/dap_engine/persistence/models.py` — 5 tabel:

- `agents` — wersjonowane definicje agentów (runtime + config + prompt template)
- `pipelines` — wersjonowane DAG-i (nodes, edges, conditions)
- `runs` — instancje wykonania (immutable FK do wersji pipeline'u)
- `state_snapshots` — snapshoty stanu po każdym node (replay/audit)
- `node_execution_logs` — stdout/stderr/prompt XML/output per node

Tabele tworzone automatycznie przy starcie engine'u (dev mode). Alembic migrations dla produkcji.

SQLite w WAL mode (`PRAGMA journal_mode = WAL`, `synchronous = NORMAL`, `foreign_keys = ON`).

## Runtime adapters

7 wbudowanych adapterów (F0 = szkielety, F3/F9 = implementacja):

| Adapter       | Kind    | Status | Uzasadnienie                                         |
| ------------- | ------- | ------ | ---------------------------------------------------- |
| `bash`        | shell   | stub   | Deterministyczne pre/post steps (pytest, cov)        |
| `http`        | http    | stub   | Wywołanie zewnętrznego endpointa                     |
| `api-call`    | api     | stub   | Direct SDK call — tanie role (selector, verifier)    |
| `claude-code` | cli     | stub   | Coding agent — testy, complex refactor               |
| `gemini-cli`  | cli     | stub   | Duży context window, szybkie operacje                |
| `codex`       | cli     | stub   | "Make tests green" loops                             |
| `aider`       | cli     | stub   | Self-directed git-aware coding                       |

Rozszerzalność przez plugin API — drop-in do `~/.dap/plugins/` (F11).

## Linter / typechecker / testy

```bash
uv run ruff check apps packages tests
uv run ruff format apps packages tests
uv run mypy apps packages tests
uv run pytest                           # smoke tests w tests/smoke/
```

## CI

Każdy PR do `develop` lub `main` jest automatycznie gateowany przez `.github/workflows/ci.yml`:

- ✅ `uv run ruff check apps packages` — blocking
- ✅ `uv run ruff format --check apps packages` — blocking
- ✅ `uv run mypy apps packages tests` — blocking
- ✅ `uv run pytest -q` — blocking (smoke tests)

Dla PR-ów do `main` dodatkowo `.github/workflows/enforce-main-source.yml` weryfikuje, że źródłem PR jest `develop` (release-only flow).

Workflow używa `astral-sh/setup-uv@v3` z cache na `uv.lock`. Concurrency: nowy push do brancha anuluje wcześniejszy bieg CI.

## Git

Repo: https://github.com/rafeekpro/dap (private).
Default branch: `develop`. Workflow: [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Zasady niezmienne (strażnicy architektury)

1. **LangGraph controls flow, Runtime executes steps.**
2. **Prompt jest kodem.** Kompilowany z template + State projection. Wersjonowany.
3. **State jest jedynym źródłem prawdy.** Runtime output parsowany do diff.
4. **Autonomia lokalna, nie globalna.** Runtime może robić tool use — ale nie decyduje *co robić dalej* w pipeline.
5. **Runtime to executor, nie planer.** Nawet claude-code dostaje skompilowany XML z konkretnym zadaniem + kontraktem wyjścia.
