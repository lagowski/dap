# DAP — Deterministic Agent Pipeline

Lokalna aplikacja do budowy i wykonywania deterministycznych pipeline'ów agentowych. Paperclip-like UX, DAP-owe zasady (state machine + XML prompts + runtime adapters).

Pełny plan architektoniczny: [`../LOCAL_APP_PLAN.md`](../LOCAL_APP_PLAN.md)
Dokumentacja koncepcyjna: [`../DOCUMENTATION.md`](../DOCUMENTATION.md)

---

## Stan implementacji

### ✅ F0 — Monorepo skeleton (ukończone 2026-04-23, zweryfikowane 2026-04-24)

Scaffolding wszystkich workspace'ów, tooling, baseline REST API, persystencja SQLite, placeholdery runtime adapters.

**Zweryfikowane end-to-end:**
- `pnpm install` — 185 paczek, ~10s
- `pnpm build` — Turbo 5/5 successful, ~4.5s
- `dap --version` → `0.0.1`
- `dap init` → tworzy `.dap/` z pełną strukturą
- `dap start` → engine na `127.0.0.1:7333`
- `GET /health` → `{"status":"ok","service":"@dap/engine","version":"0.0.1",...}`
- `GET /runtimes` → lista 7 adapterów (bash, http, api-call, claude-code, gemini-cli, codex, aider)
- `GET /runtimes/:id/health` → healthcheck per runtime (bash ✓ available, cli agents ✗ missing binary)
- SQLite `state.db` utworzony, WAL mode aktywny (`state.db-shm`, `state.db-wal`)

### Planowane

| Faza  | Zakres                                                              |
| ----- | ------------------------------------------------------------------- |
| F1    | CLI process management, stop/status, port discovery                 |
| F2    | Engine: REST CRUD dla agents/pipelines/runs                         |
| F3    | Runtime adapters 1st wave — `api-call`, `bash`, `claude-code`       |
| F4    | Prompt Builder (Nunjucks → XML, schema validator)                   |
| F5    | Pipeline execution (LangGraph.js bootstrap, state diff, checkpointing) |
| F6    | Dashboard viewer (Runs List, Run Detail, Node Drawer)               |
| F7    | Dashboard Pipeline Designer                                         |
| F8    | Agent & runtime registry UI                                         |
| F9    | Runtime adapters 2nd wave — `gemini-cli`, `codex`, `aider`, `http`  |
| F10   | New Run wizard + GitHub PAT integration                             |
| F11   | Polish: keychain, export/import, dark mode                          |
| F12   | Packaging, `npm publish`, cross-platform verification               |

---

## Struktura monorepo

```
dap/
├─ .gitignore, .nvmrc, README.md
├─ package.json                    root (pnpm workspaces + Turbo)
├─ pnpm-workspace.yaml
├─ turbo.json
├─ tsconfig.base.json              strict TS, ES2022, composite
│
├─ apps/
│  ├─ cli/                         @dap/cli — Node launcher (commander)
│  │  └─ src/
│  │     ├─ index.ts               (dap init|start|stop|status|--version)
│  │     ├─ paths.ts               (.dap/, config.json, state.db)
│  │     └─ commands/              (init, start, stop, status)
│  │
│  ├─ engine/                      @dap/engine — Fastify + LangGraph.js + Drizzle
│  │  └─ src/
│  │     ├─ index.ts               createEngine factory
│  │     ├─ bin.ts                 standalone entry
│  │     ├─ api/                   (health, runtimes)
│  │     └─ persistence/
│  │        ├─ schema.ts           agents, pipelines, runs, snapshots, logs
│  │        └─ db.ts               better-sqlite3 + drizzle, WAL mode
│  │
│  └─ dashboard/                   @dap/dashboard — placeholder (F6)
│
└─ packages/
   ├─ types/                       @dap/types — Agent, Pipeline, Run, State, Runtime
   └─ runtimes/                    @dap/runtimes — RuntimeAdapter interface + registry
      └─ src/adapters/             bash, http, api-call, claude-code,
                                   gemini-cli, codex, aider  (stuby F0)
```

## Tech stack

| Warstwa               | Wybór                                 |
| --------------------- | ------------------------------------- |
| Runtime               | Node.js 22 LTS                        |
| Language              | TypeScript 5.6 (strict)               |
| Monorepo              | pnpm 9 workspaces + Turborepo 2       |
| CLI                   | commander.js 12                       |
| Engine HTTP           | Fastify 5                             |
| State machine         | @langchain/langgraph (F5+)            |
| DB                    | better-sqlite3 + Drizzle ORM          |
| Subprocess            | execa 9                               |
| Schema validation     | Zod                                   |
| Dashboard (F6)        | Next.js 15 + shadcn/ui + React Flow   |

---

## Quickstart

Wymagania: Node 22+, pnpm 9+.

```bash
cd /Users/rla/RLA02/PROJEKTY/Developer/dap

pnpm install             # instaluje deps, linkuje workspace'y
pnpm build               # Turbo buduje packages w kolejności zależności

# Test CLI
node apps/cli/dist/index.js --version       # → 0.0.1
node apps/cli/dist/index.js --help

# Test pełnego flow
mkdir -p /tmp/dap-playground && cd /tmp/dap-playground
node /Users/rla/RLA02/PROJEKTY/Developer/dap/apps/cli/dist/index.js init
node /Users/rla/RLA02/PROJEKTY/Developer/dap/apps/cli/dist/index.js start
# Ctrl+C żeby zatrzymać

# Po F12 (packaging): npm install -g @dap/cli → `dap init`
```

## Dostępne komendy CLI (F0)

| Komenda       | Status   | Opis                                                   |
| ------------- | -------- | ------------------------------------------------------ |
| `dap --version` | ✅     | Wersja binarki                                         |
| `dap --help`  | ✅       | Lista komend                                           |
| `dap init`    | ✅       | Tworzy `./.dap/` z config.json i podkatalogami         |
| `dap start`   | ✅ partial | Spawn engine na 127.0.0.1:7333 (dashboard: F6)       |
| `dap stop`    | 🚧 stub  | Wymaga PID management (F1)                             |
| `dap status`  | ✅ partial | Pokazuje czy projekt zainicjowany (runtime info: F1) |

## Dostępne endpointy engine (F0)

Po `dap start`:

```bash
curl http://127.0.0.1:7333/health
# → { "status": "ok", "service": "@dap/engine", "version": "0.0.1", ... }

curl http://127.0.0.1:7333/runtimes
# → lista 7 zarejestrowanych adapterów z kind (cli/api/shell/http)

curl http://127.0.0.1:7333/runtimes/bash/health
# → { "available": true, "version": "system" }

curl http://127.0.0.1:7333/runtimes/claude-code/health
# → { "available": false, "missing": ["claude binary ..."] }
```

## Baza danych

Drizzle schema w `apps/engine/src/persistence/schema.ts` definiuje 5 tabel:

- `agents` — wersjonowane definicje agentów (runtime + config + prompt template)
- `pipelines` — wersjonowane DAG-i (nodes, edges, conditions)
- `runs` — instancje wykonania (immutable FK do wersji pipeline'u)
- `state_snapshots` — snapshoty stanu po każdym node (replay/audit)
- `node_execution_logs` — stdout/stderr/prompt XML/output per node

Generowanie migracji:
```bash
cd apps/engine
pnpm db:generate
```

SQLite w WAL mode (`journal_mode = WAL`, `synchronous = NORMAL`, `foreign_keys = ON`).

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

## Git

Repo zainicjowane (`git init`), **brak initial commita** — czeka na zgodę użytkownika.

```bash
git status     # zobacz co zostanie dodane
```

## Zasady niezmienne (strażnicy architektury)

Powtórzone tu dla implementatorów — patrz pełny opis w `../DOCUMENTATION.md`:

1. **LangGraph controls flow, Runtime executes steps.**
2. **Prompt jest kodem.** Kompilowany z template + State projection. Wersjonowany.
3. **State jest jedynym źródłem prawdy.** Runtime output parsowany do diff.
4. **Autonomia lokalna, nie globalna.** Runtime może robić tool use — ale nie decyduje *co robić dalej* w pipeline.
5. **Runtime to executor, nie planer.** Nawet claude-code dostaje skompilowany XML z konkretnym zadaniem + kontraktem wyjścia.
