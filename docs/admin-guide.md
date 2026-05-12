# Admin operator guide

This document walks through every action the instance admin can take
from the dashboard's `/admin/*` surface, plus the recovery paths when
something goes wrong. Aimed at someone running DAP for a 5-20 person
team — single instance, shared database, you wear the admin hat.

Out of scope here: provider-app setup (see
[`auth.md`](auth.md)) and deployment / env vars (see
[`self-hosting.md`](self-hosting.md)). The security posture
itself — what we protect against, what we don't — lives in
[`security.md`](security.md).

## First-run flow

A fresh DAP instance has zero users. The instance refuses every API
call until at least one admin exists.

### Bootstrap the admin

`dap init` creates the admin row (or promotes an existing email)
idempotently. Three input modes:

```bash
# 1. Flag-driven (quick, password lands in shell history).
dap init --admin-email=you@example.com --admin-password=hunter12345

# 2. Stdin (kubectl-style — automation-friendly).
echo 'hunter12345' | dap init --admin-email=you@example.com --admin-password-stdin

# 3. Interactive (getpass + confirmation, no echo).
dap init --admin-email=you@example.com
#   → "Admin password (leave empty to auto-generate a random one): "
#   → "Re-enter password: "
```

Leaving the password empty in interactive mode triggers a generated
random — printed once to stdout. You then change it via
`/admin/users` once you're logged in.

`dap init` reads `DAP_DB_PATH` so it bootstraps against the right
SQLite even inside the Docker container (where compose sets it to
`/data/state.db`). For Postgres deployments, run `dap init`
against the engine machine; the row writes through any
SQLAlchemy-supported DB.

### What the bootstrap admin can do

Everything. There is no role boundary above admin in v0.3 — an
admin can list, edit, suspend, soft-delete any user, revoke any
API token, read the full audit log, and modify their own row.

The bootstrap admin's permissions match every subsequent admin's;
they're not a special "superadmin". Promoting a second user via
`/admin/users` gives them an identical scope.

### The `.dap/bootstrap.json` marker

`dap init` writes a small file next to the database (chmod 600) so
`dap status` can confirm the bootstrap happened:

```
.dap/
├── state.db          # SQLite (default backend)
├── bootstrap.json    # {"email":"...","user_id":"...","created_at":"...","promoted_existing":false}
└── config.json       # engine host / port, dashboard port
```

No secret material in `bootstrap.json` — the generated password is
only ever printed to stdout. `dap status` reads this file and
reports:

```
✓ admin bootstrap: you@example.com (2026-05-12T10:00:00+00:00)
● engine: running  PID 12345 port 7333 uptime 3h12m
```

When the marker is missing but `.dap/` exists, status shows a hint:
`○ admin bootstrap: none — run \`dap init\` to create one`.

## User management — `/admin/users`

The admin user table. Underlying engine endpoints:

- `GET /users` (admin-only) — paginated list with filters.
- `POST /auth/register` — create a new user (any role can call;
  the new user is non-admin by default).
- `PATCH /users/{id}` — edit fields including `is_superuser`,
  `is_active`, email, password.
- `DELETE /users/{id}` — soft delete (sets `deleted_at`).

Non-admin users hitting these endpoints get **404** (anti-enumeration),
not 403 — matches the rest of the ownership-aware surface so
attackers can't probe whether an admin endpoint exists.

### Create

`/admin/users/new` form fields:

- **Email** — must be unique, validated server-side by
  fastapi-users' `EmailStr`.
- **Password** — 8+ chars. Engine hashes with Argon2id via pwdlib.
- **Admin** — checkbox; checked → `is_superuser=True`.

The dashboard's form sends `POST /auth/register` then a
`PATCH /users/{id}` to flip `is_superuser` when needed (fastapi-users
doesn't accept `is_superuser` on register, by design).

### Edit

`/admin/users/{id}` exposes:

- **Email** — changing it invalidates any pending OAuth identity
  matches; the user must re-link.
- **Password** — admin sets a temporary value; user rotates via
  `/auth/reset-password` (with the temp password) or
  `/admin/users` again.
- **Role** — toggles `is_superuser`.
- **Active** — toggles `is_active`. False → user can't log in, but
  audit history and owned resources are preserved.

### Suspend vs. delete

- **Suspend** (`is_active=False`): user can't log in but their row
  is still visible in the admin list (without the soft-deleted
  filter on) and owns their resources normally.
- **Soft delete** (`deleted_at NOT NULL`): row is hidden from
  default `/admin/users` listings unless you toggle
  "include deleted". Owned resources stay attached to the deleted
  user — visible to admins for audit, invisible to other users.

Hard delete (drop the row + all owned resources) is **out of
scope** in v0.3. If you need it: stop the engine, open the SQLite
file with `sqlite3`, run `DELETE FROM users WHERE id=...` after
nulling out the FKs in `pipelines`, `agents`, `projects`,
`runs`, `audit_log`. A managed UI for this lands in v0.4.

### Promote a normal user to admin

Two paths:

1. **From the UI**: `/admin/users/{id}` → toggle the **Admin**
   checkbox → Save. Fires `PATCH /users/{id}` with
   `is_superuser=true`.
2. **From the CLI**: `dap init --force --admin-email=existing@user
   --admin-password=...`. The bootstrap is idempotent — it
   detects the existing row and promotes it. Useful when the only
   admin lost their password (see *Recovery* below).

## Audit log — `/admin/audit-log`

Append-only event ledger fed by the auth subsystem and the
ownership repositories. Underlying endpoint:
`GET /audit/events?event_type=...&user_id=...&offset=...&limit=...`.

### Event types currently emitted

| Event type | Fires when |
|---|---|
| `user.registered` | `POST /auth/register` succeeds |
| `user.logged_in` | `POST /auth/jwt/login` succeeds |
| `user.forgot_password` | `POST /auth/forgot-password` (token issued) |
| `user.password_reset` | `POST /auth/reset-password` succeeds |
| `user.deleted` | Soft-delete via `DELETE /users/{id}` |
| `api_token.created` | `POST /auth/api-tokens` mint |
| `api_token.revoked` | `DELETE /auth/api-tokens/{id}` or `/admin/{id}` |
| `agent.created` / `.updated` / `.archived` | Lifecycle on `/agents` |
| `pipeline.created` / `.updated` / `.archived` | Lifecycle on `/pipelines` |
| `project.created` / `.updated` / `.archived` | Lifecycle on `/projects` |
| `run.triggered` | `POST /runs` |

Each row carries:

- `id` (UUID)
- `user_id` — the **acting** user (admin doing the deletion, owner
  triggering the run, etc.). Nullable for system-emitted events.
- `event_type` (string above)
- `event_data` (JSON) — varies per event type; typically includes
  the target row id and any relevant before/after deltas.
- `created_at`

### Filters

- **`event_type`** — exact match (e.g. `user.logged_in`).
- **`user_id`** — filter to events triggered by a specific user.
- **`offset` + `limit`** — pagination (`limit` max 500).

Default sort is `created_at DESC` so freshest first.

### Retention

Append-only, **no automatic pruning** in v0.3. The audit log will
grow forever otherwise — for now the recommended cleanup is
manual SQL once the file gets large:

```bash
sqlite3 .dap/state.db \
    "DELETE FROM audit_log WHERE created_at < datetime('now', '-180 days');"
```

Operators with compliance requirements should keep everything
(append-only is the audit-friendly default). A retention CLI
(`dap audit prune --older-than 180d`) lands in v0.4.

## API tokens — `/admin/api-tokens`

Admin-wide view of every API token across all users. Underlying
engine endpoints:

- `GET /auth/api-tokens/admin` — admin-wide list.
- `DELETE /auth/api-tokens/admin/{id}` — revoke any user's
  token.

The page surfaces:

- Token `prefix` (e.g. `dap_abc1`) — for identification.
- Owner email + display name.
- `name` label set at creation.
- `created_at` / `last_used_at` / `expires_at` / `revoked_at`.

Revocation fires `api_token.revoked` audit events carrying both
the acting admin's `user_id` and the target token's owner — so
the log clearly distinguishes self-revocations from admin
forced revocations.

See [`auth.md`](auth.md#api-tokens) for the per-user token
lifecycle (mint, list, self-revoke).

## Recovery procedures

### Lost admin password

Two recovery paths depending on whether you can still reach the
engine machine:

**1. From a shell on the engine host** — re-run `dap init`. The
command is idempotent: an existing email is *promoted* to admin
(with the new password) without rolling back the user's data.

```bash
# Local dev / single-machine
cd /path/to/.dap/parent
dap init --force --admin-email=you@example.com --admin-password=<new>

# Docker
docker compose -f examples/standalone/docker-compose.yml exec dap \
    dap init --force --admin-email=you@example.com --admin-password=<new>
```

`--force` is required because `.dap/` already exists; without it
`dap init` refuses to clobber.

**2. From raw SQL** — if `dap` isn't installed but you have DB
access. Generate a new bcrypt-compatible hash (Argon2id is
preferred but writes are simpler if you use a bcrypt-shaped hash
since fastapi-users' PasswordHelper accepts both):

```bash
python3 -c "
from fastapi_users.password import PasswordHelper
print(PasswordHelper().hash('your-new-password'))
"
# → '\$argon2id\$v=19\$m=65536,t=3,p=4\$...'

sqlite3 .dap/state.db \
    "UPDATE users SET hashed_password='<hash>', is_active=1 WHERE email='you@example.com';"
```

Log in with `your-new-password`; rotate from `/admin/users` once
you're in.

### Lost JWT secret / suspected key compromise

Rotate `DAP_AUTH_JWT_SECRET` and restart the engine. Every
outstanding cookie session is invalidated — every user gets logged
out and needs to log in again.

```bash
# Mint a new secret.
NEW=$(openssl rand -hex 32)

# Replace it in .env and restart.
sed -i "s/^DAP_AUTH_JWT_SECRET=.*/DAP_AUTH_JWT_SECRET=$NEW/" .env
docker compose down && docker compose up -d
```

**Important:** API tokens (the opaque `dap_*` values) are **NOT**
signed with the JWT secret — they're hashed in the DB. Rotating
the JWT secret leaves API tokens working. If the compromise scope
includes API tokens, revoke them from `/admin/api-tokens` after
the JWT rotation.

There's no in-place rolling rotation in v0.3 (would require a
secret-version column on the JWT payload + concurrent validation
against old + new keys); operators willing to accept a brief mass
logout do the swap-and-restart above.

### Corrupted SQLite

Before any migration or upgrade, take a backup:

```bash
# Hot-copy with sqlite3's online backup API.
sqlite3 .dap/state.db ".backup '/path/to/backup-$(date +%Y%m%d).db'"

# Or .dump → SQL text if you want it grep-able.
sqlite3 .dap/state.db ".dump" > backup-$(date +%Y%m%d).sql
```

To restore from a SQL dump:

```bash
mv .dap/state.db .dap/state.db.corrupted
sqlite3 .dap/state.db < backup-2026-05-12.sql
```

Migrations are forward-only — there's no automatic down-migration
support. If you need to roll back to a prior schema, restore the
last backup taken before the offending migration ran.

### Engine refuses to start with "Migrated single-user install" log

That's the pre-v0.3 upgrade path (#343) firing on a fresh
`apply_migrations` against a `.dap/state.db` that pre-dates the
multi-user schema. The migration prints a generated password
exactly once on stdout:

```
Migrated single-user install. Bootstrap admin: legacy-admin@local
with password=<random>. Change immediately at /admin/users.
```

Capture the password, log in as `legacy-admin@local`, and either
rotate the password or rename the account via `/admin/users`. The
password is **not** persisted anywhere — if you miss it, re-bootstrap
via `dap init --force --admin-email=legacy-admin@local
--admin-password=<new>`.

## Where to look when things break

| Symptom | Look at |
|---|---|
| Bootstrap admin can log in but doesn't see "Admin" link in dashboard | `is_superuser=False`. Re-run `dap init --force --admin-email=...` (idempotent promotion) or flip the flag in SQL. |
| `/admin/users` returns 404 | Currently logged-in user isn't an admin. Anti-enumeration — 404 instead of 403. |
| `/admin/audit-log` empty | Engine started fresh; no events yet. Trigger a login or resource create. |
| Audit log entry has `user_id: null` | System-emitted event (migration, internal). The acting principal is the engine itself. |
| API token shows up in `/admin/api-tokens` but auth still fails | Check `revoked_at` and `expires_at`. Revoked / expired tokens stay in the list (for audit), they just stop authenticating. |
| `dap init` works but engine starts without the admin | The `dap init` CWD didn't match where the engine is reading from. Verify `DAP_DB_PATH` — both processes need to point at the same SQLite. |

See also:

- [`auth.md`](auth.md) — how each credential mechanism works.
- [`self-hosting.md`](self-hosting.md) — deployment + env vars.
- [`security.md`](security.md) — secrets handling, threat model.
