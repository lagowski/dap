# Code Review Council: a GitHub Action

AI reviewers that flag invented concerns waste reviewers' time. The Code Review Council runs several
**scoped** specialists on a pull request (security, correctness, database, performance, frontend; each one
skips a diff outside its scope) and then a **final arbiter** that drops duplicates, low-confidence
"what if" findings and anything your project marks out of scope. What survives is posted as one PR review
comment.

The council is `packages/code-review-council` in this repo; DAP's own review gate uses it.

## Copy-paste workflow

Save as `.github/workflows/code-review-council.yml` in your repository, and add a repository secret
`GEMINI_API_KEY` (a Google AI Studio key) or `OPENROUTER_API_KEY`:

```yaml
name: Code Review Council

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]

permissions:
  contents: read
  pull-requests: write

concurrency:
  group: code-review-council-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  review:
    if: ${{ !github.event.pull_request.draft }}
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      # Only needed when you pass a context-file.
      - uses: actions/checkout@v4

      - uses: lagowski/dap/review@v0.4.0   # pin a release tag or a commit SHA
        with:
          provider: gemini                  # or: openrouter
          api-key: ${{ secrets.GEMINI_API_KEY }}
          # model: gemini-2.5-flash         # empty = the provider's default
          # context-file: .github/review-context.toml
          fail-on: none                     # or: high
```

The action is available from the first release that ships it (`v0.4.0`). Until then, pin a commit SHA of
the `develop` branch.

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `provider` | `gemini` | `gemini` (Google AI Studio) or `openrouter` (any model OpenRouter hosts). |
| `model` | provider default | Model name, e.g. `gemini-2.5-flash` or an OpenRouter id like `deepseek/deepseek-chat`. |
| `api-key` | required | The provider key. Pass a secret; the action hands it to the council through an environment variable and never prints it. |
| `context-file` | none | Project context that steers the arbiter (see below). Path relative to the workspace, so check out first. |
| `fail-on` | `none` | `high` fails the job when a HIGH or CRITICAL finding survives the arbiter. The review is posted first. |
| `github-token` | `github.token` | Reads the PR diff and posts the review. Needs `pull-requests: write`. |
| `python-version` | `3.13` | Python used to run the council (3.13 or newer). |

Outputs: `verdict` (`comment` or `request_changes`), `findings` (count) and `json-file` (path of the full
JSON verdict, for later steps).

## Project context

The context file is the main lever against invented findings: anything listed under `out_of_scope` is
dropped by the arbiter. TOML (or the same keys in JSON):

```toml
stack = ["Python 3.13", "FastAPI", "PostgreSQL", "Next.js 16"]
in_scope = ["authentication and authorization", "data correctness", "SQL safety"]
out_of_scope = ["i18n", "CSP nonces", "docstring style"]
notes = "Internal tool behind SSO; the public API is read-only."
```

Any other file type (for example a Markdown page) is passed to the reviewers as free-form notes.

## Forks

On `pull_request` from a fork, `github.token` is read-only and the secret is not available, so the review
can't run. Don't switch to `pull_request_target` to get around it without a maintainer gate (a label such as
`safe-to-review` that a maintainer adds after reading the diff): that event runs with your secrets.

## Run it locally

The same council is a CLI:

```bash
pip install ./packages/code-review-council       # from a checkout of this repo
export GEMINI_API_KEY=...                         # or OPENROUTER_API_KEY with --provider openrouter

code-review-council --base origin/main --head HEAD --context-file review-context.toml
git diff main... | python -m code_review_council --format markdown
code-review-council --diff change.diff --format json --fail-on high
```

It prints the arbitrated verdict as JSON and then as Markdown (`--format json|markdown|none`;
`--json-out` / `--markdown-out` also write files). Exit codes: `0` reviewed, `1` a HIGH/CRITICAL finding
survived with `--fail-on high`, `2` a configuration error such as a missing API key.
