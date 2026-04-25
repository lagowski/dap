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
- ✅ **CI green** — `.github/workflows/ci.yml` wymaga: ruff check, ruff format, pytest
- ✅ `uv run ruff check apps packages tests` clean
- ✅ `uv run ruff format --check apps packages tests` clean
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

Repo instaluje **pre-push hook** blokujący bezpośredni push do `main` i `develop` (patrz `.githooks/pre-push`). Hook aktywny po:

```bash
git config core.hooksPath .githooks
```

Wykonaj raz po sklonowaniu repo. Hook nie zastępuje branch protection po stronie GitHub — jest tylko local safety net.

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
