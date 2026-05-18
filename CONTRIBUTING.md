# Contributing

## Branching model

Projekt używa **gitflow-like** workflow z trzema poziomami:

```
main     ←── PR ──   develop   ←── PR ──   issue/<number>-<slug>
(prod)              (integration)         (feature / fix branches)
```

### Zasady

1. **Nigdy nie commituj bezpośrednio do `main` ani `develop`.**
2. **Każda zmiana zaczyna się od Issue.** Branch tworzysz z `develop`, nazywasz od numeru issue.
3. **Merge do `develop` tylko przez Pull Request** z feature/issue branch. Minimum 1 review (w single-user projekcie: self-review + CI green).
4. **Merge do `main` tylko przez Pull Request z `develop`.** Reprezentuje to release.
5. **Force push do `main` / `develop` — zabronione.**
6. **Squash merge lub rebase merge** preferowane (zachowaj liniową historię); merge commits tylko dla release'ów `develop → main`.

## Workflow krok po kroku

### 1. Zaczynasz pracę nad nowym ticketem

```bash
# Zaktualizuj develop
git checkout develop
git pull --ff-only

# Utwórz branch od Issue
git checkout -b issue/42-add-http-adapter
```

Konwencja nazewnictwa branch'y:
- `issue/<nr>-<slug>` — prace z Issue (najczęstsze)
- `fix/<nr>-<slug>` — hotfix
- `chore/<slug>` — tooling, CI, dependencies (bez Issue)
- `docs/<slug>` — sama dokumentacja

### 2. Commity

Używaj **Conventional Commits**:

```
<type>(<scope>): <subject>

[body]

[footer]
```

Types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `style`, `ci`, `build`.

Scope (opcjonalnie): `cli`, `engine`, `runtimes`, `types`, `dashboard`, `docs`, `ci`.

Przykłady:
```
feat(runtimes): implement claude-code adapter with JSON output parsing
fix(engine): handle SQLite WAL checkpoint on shutdown
docs(readme): update F0 status after E2E verification
```

Każdy commit powinien być **logically atomowy** — przechodzić build + testy.

### 3. Pull Request

Push branch do origin i otwórz PR → `develop`:

```bash
git push -u origin issue/42-add-http-adapter
gh pr create --base develop --fill
```

PR template wypełni się automatycznie (patrz `.github/PULL_REQUEST_TEMPLATE.md`).

**Definition of Done** dla PR do `develop`:
- ✅ Wszystkie commity Conventional Commits
- ✅ **CI green** — `.github/workflows/ci.yml` wymaga: ruff check, ruff format, mypy, pytest
- ✅ `uv run ruff check apps packages tests` clean
- ✅ `uv run ruff format --check apps packages tests` clean
- ✅ `uv run mypy apps packages tests` clean
- ✅ `uv run pytest` passes (smoke testy w `tests/smoke/`, dodaj swoje testy gdy dotyczy)
- ✅ Dashboard (gdy zmieniany): `pnpm --dir apps/dashboard build` passes
- ✅ PR linkuje Issue (`Closes #42`)
- ✅ Zaktualizowane relevantne docs (README, plan faz, etc.)

> **Local equivalence:** komendy CI są dokładnie te same co w DoD wyżej. Jeśli zielone lokalnie — zielone w CI (modulo różnice OS, ale runner używa Ubuntu, więc trzymaj Linux-friendly).

### 4. Release: develop → main

```bash
gh pr create --base main --head develop --title "release: vX.Y.Z"
```

**Definition of Done** dla PR do `main`:
- ✅ Wszystkie wymagania z PR do `develop`
- ✅ CHANGELOG.md zaktualizowany
- ✅ Wersje w `apps/*/pyproject.toml` i `packages/*/pyproject.toml` podbite zgodnie z semver
- ✅ Tag release'u utworzony po merge (`git tag vX.Y.Z && git push --tags`)

## Lokalne zabezpieczenia

### Root npm scripts

Root `package.json` nie jest osobnym pakietem aplikacyjnym. Służy jako
lekki orkiestrator dla najczęstszych lokalnych komend:

```bash
npm run lint       # pnpm --dir apps/dashboard lint
npm run typecheck  # pnpm --dir apps/dashboard typecheck
npm run test       # pnpm --dir apps/dashboard test
npm run build      # pnpm --dir apps/dashboard build
npm run e2e:test   # npm --prefix e2e test
```

Dashboard nadal używa `pnpm` i `apps/dashboard/pnpm-lock.yaml`; Playwright e2e
nadal używa `npm` i `e2e/package-lock.json`. Nie instaluj zależności dashboardu
w root `node_modules`.

### Dashboard API types

Dashboard API contracts are generated from the engine FastAPI OpenAPI schema.
The generator does not require a running engine server; it imports the app and
exports OpenAPI locally.

```bash
pnpm --dir apps/dashboard gen:api    # rewrite src/lib/api/types.gen.ts
pnpm --dir apps/dashboard check:api  # CI drift check
```

Run `gen:api` whenever engine request/response schemas change and commit the
resulting `apps/dashboard/src/lib/api/types.gen.ts`. CI runs `check:api` before
dashboard typecheck/lint/test/build, so stale generated contracts fail the PR.

### Pre-push hook (blokada main / develop + format guard)

Repo dostarcza **pre-push hook** (patrz `.githooks/pre-push`) który robi dwie rzeczy:

1. **Blokuje bezpośredni push do `main` / `develop`** — wymusza gitflow workflow z PR-em.
2. **Uruchamia `pre-commit run --all-files` przed pushem** (jeśli `pre-commit` jest zainstalowane lokalnie). Łapie format-fix-y które prześlizgnęły się przez sam `pre-commit install`.

   Mechanizm autofix-skipu: gdy hook commit'a zmieni plik (np. `ruff-format`), pre-commit przerywa *bieżący* commit i zostawia naprawione pliki w roboczym drzewie (nie zaindexowanym). Jeśli operator ponowi `git commit` **bez** uprzedniego `git add`, drugi commit przechodzi na *starym* indexie — niesformatowanej wersji — ponieważ z perspektywy pre-commit hook'a "tym razem nic nie zmienił". Ta druga forma diffa dolatuje do origin i wywala CI dopiero zdalnie. Push-time check zamyka tę lukę.

Aktywacja po sklonowaniu repo:

```bash
git config core.hooksPath .githooks
```

Bypass (np. emergency hotfix lub praca w branchu z WIP-em który nie powinien być formatowany przed push'em):

```bash
git push --no-verify
```

Hook nie zastępuje branch protection po stronie GitHub — jest tylko local safety net + accelerator feedback'u.

### Pre-commit hooks (ruff + mypy)

Repo używa [`pre-commit`](https://pre-commit.com) do uruchomienia tych samych checków co CI lokalnie przed każdym commitem (ruff lint + format, mypy strict). Konfiguracja w `.pre-commit-config.yaml`. Instalacja:

```bash
# 1. Najpierw skonfiguruj hooksPath (włącza pre-push hook z .githooks/).
git config core.hooksPath .githooks

# 2. Potem zainstaluj pre-commit.
uv tool install pre-commit
pre-commit install
```

**Kolejność jest ważna.** Gdy `core.hooksPath` jest ustawione, Git ignoruje `.git/hooks/` całkowicie. `pre-commit install` wykrywa custom hooksPath i instaluje hook `pre-commit` do tego samego katalogu (`.githooks/pre-commit`), więc oba hooki — `pre-push` (zatwierdzony w repo) i `pre-commit` (instalowany lokalnie, gitignored) — żyją razem w `.githooks/`.

Pierwsze `pre-commit run --all-files` zbuduje izolowane środowiska dla hooków ruff (kilka sekund); kolejne uruchomienia są błyskawiczne. Mypy używa lokalnego `uv run mypy`, więc dziedziczy workspace deps (bez tego `--strict` daje false positives).

## Remote branch protection (GitHub)

Po podłączeniu remote do GitHub, wymagane są **branch protection rules**:

### `main`
- Require a pull request before merging
- Require approvals: 1 (lub review from Code Owners)
- Restrict who can push: nobody (w tym admins)
- Allow force pushes: ❌
- Allow deletions: ❌
- Require status checks: `build`, `typecheck`, `test` (CI)
- Restrict merges: **tylko z `develop`** (enforced via CODEOWNERS + linear history)

### `develop`
- Require a pull request before merging
- Require approvals: 1 (self-review OK w single-user)
- Allow force pushes: ❌
- Allow deletions: ❌
- Require status checks: `build`, `typecheck`, `test`

Komendy `gh` do konfiguracji dostępne w `docs/github-setup.md` (TBD).

## Konwencje dodatkowe

- **Język kodu:** Python 3.13 (strict ruff/mypy); dashboard w TypeScript strict
- **Język komentarzy i commitów:** angielski
- **Język dokumentacji user-facing** (README, CONTRIBUTING): polski (zgodnie z preferencją projektu)
- **Issue w repo:** polski OK, angielski też OK
- **Imports:** ruff isort konfiguracja w `pyproject.toml` — używaj `uv run ruff format`
