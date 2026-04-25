# dap-cli

Typer-based CLI launcher dla DAP. Spawn engine + dashboard, podobnie jak `npx paperclip`.

## Komendy (F0)

| Komenda          | Status      | Opis                                                       |
| ---------------- | ----------- | ---------------------------------------------------------- |
| `dap --version`  | ✅          | Wersja                                                     |
| `dap --help`     | ✅          | Lista komend                                               |
| `dap init`       | ✅          | Tworzy `./.dap/` z config.json i podkatalogami             |
| `dap start`      | ✅ partial  | Spawn engine na 127.0.0.1:7333 (dashboard: F6)             |
| `dap stop`       | 🚧 stub     | F1 (PID management)                                        |
| `dap status`     | ✅ partial  | Pokazuje czy projekt zainicjowany                          |

## Lokalne uruchomienie z monorepo

```bash
uv sync
uv run dap --version
uv run dap init
uv run dap start
```

## Po opublikowaniu (F12)

```bash
uv tool install dap-cli
dap init && dap start
```
