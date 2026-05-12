# Authentication

DAP supports four credential mechanisms, all routing through the same
`fastapi-users` auth backend. Pick whichever fits your team:

| Mechanism | Best for | Where it lives |
|---|---|---|
| Email + password | Self-hosted teams, default | Engine: `/auth/register`, `/auth/jwt/login` |
| GitHub OAuth | Teams already on GitHub | Engine: `/auth/github/authorize`, `/auth/github/callback` |
| Google OAuth | Teams on Google Workspace | Engine: `/auth/google/authorize`, `/auth/google/callback` |
| API tokens | CLI, scripts, CI | Engine: `/auth/api-tokens` |

Cookie-based JWT sessions (`dap-jwt`, httpOnly) drive the dashboard;
the same JWT works as a bearer token against the engine REST API.
Opaque `dap_*` tokens cover scripts and machine-to-machine traffic.

---

## Email + password

### Self-signup

`POST /auth/register` with `{email, password}`. Engine returns the new
`User` row. The dashboard's `/signup` page is the easy path:

```bash
curl -X POST http://localhost:7333/auth/register \
    -H 'content-type: application/json' \
    -d '{"email":"alice@example.com","password":"hunter12345"}'
```

Policy: 8+ character password (validated server-side by
`UserManager.validate_password`). No complexity rules, no breach
checks. The first user must be promoted to admin (see
[`admin-guide.md`](admin-guide.md)) — or operators can bootstrap an
admin from the CLI before opening signup with `dap init
--admin-email=...`.

### Login

`POST /auth/jwt/login` with form-encoded `username` + `password` (the
fastapi-users default). Returns a JWT — the dashboard's
`/api/auth/login` route handler converts that into the `dap-jwt`
httpOnly cookie:

```bash
curl -X POST http://localhost:7333/auth/jwt/login \
    -d 'username=alice@example.com&password=hunter12345'
# → {"access_token":"eyJ…","token_type":"bearer"}
```

Tokens default to a 15-minute lifetime; tune via
`DAP_AUTH_ACCESS_TTL_SECONDS`. There is no refresh-token flow — the
short TTL bounds revocation latency without one.

### Password reset

The fastapi-users reset router is mounted; the engine ships **no
SMTP integration** in v0.3. Two operational paths:

**1. Admin-driven (recommended).** From `/admin/users` an admin
edits the target user and sets a fresh password. The user logs in
with the new password and rotates it themselves.

**2. Self-service with `DAP_AUTH_LOG_RESET_TOKENS=1`.** Useful for
local-dev / single-operator instances without mail delivery. The
user posts to `/auth/forgot-password` with their email, the engine
emits the raw reset token at WARNING level in the engine logs (the
same stream `logging.basicConfig` writes to — stderr by default,
journald or `docker logs` under systemd / Docker), the operator
copies it out, and the user submits it back via
`/auth/reset-password`:

```bash
# User triggers
curl -X POST http://localhost:7333/auth/forgot-password \
    -H 'content-type: application/json' \
    -d '{"email":"alice@example.com"}'
# Engine logs line (default format
#   "%(asctime)s %(levelname)-7s %(name)s | %(message)s"):
#   12:34:56 WARNING dap.engine.auth | user.forgot_password (token issued, email delivery TODO): user_id=<uuid> reset_token=<token>

# User submits the token (operator hands it over)
curl -X POST http://localhost:7333/auth/reset-password \
    -H 'content-type: application/json' \
    -d '{"token":"<token>","password":"new-password-12345"}'
```

Grep target: ``dap.engine.auth | user.forgot_password``.

**Never leave `DAP_AUTH_LOG_RESET_TOKENS=1` in production.** Reset
tokens are account-takeover credentials; log aggregation routinely
ingesting them is a serious leak. The default is off.

---

## GitHub OAuth

### One-time setup

1. Go to **GitHub → Settings → Developer settings → OAuth Apps → New OAuth App**.
2. Fill in:
   - **Application name**: `DAP` (or your team's preferred name).
   - **Homepage URL**: `https://<your-dashboard>` (e.g.
     `https://dap.example.com`).
   - **Authorization callback URL**:
     `https://<your-engine>/auth/github/callback`.
3. Click **Generate a new client secret** and copy both values.

Scopes requested by the engine: `read:user user:email`. GitHub asks
the user to grant these on first login; once granted, subsequent
logins are silent.

### Engine config

```bash
# .env
DAP_OAUTH_GITHUB_CLIENT_ID=<client-id-from-github>
DAP_OAUTH_GITHUB_CLIENT_SECRET=<client-secret-from-github>
DAP_AUTH_OAUTH_REDIRECT_URL=https://dap.example.com/api/auth/oauth/callback
```

The OAuth router only mounts when **both** `CLIENT_ID` and
`CLIENT_SECRET` are set — partial config disables the provider
without an error.

`DAP_AUTH_OAUTH_REDIRECT_URL` is the *dashboard* URL the engine
redirects the user to after the provider authenticates them. The
dashboard's catch-all `/api/auth/oauth/callback` handler reads the
`?token=` query parameter, sets the `dap-jwt` cookie, and lands the
user on the home page. Without it the engine returns the token as
JSON — fine for CLI flows, useless for the dashboard.

### Login flow

The dashboard's `/login` page renders a **Sign in with GitHub**
button when the env vars are set. Behind it:

1. Dashboard → `/auth/github/authorize` → engine
2. Engine → GitHub authorize page → user grants
3. GitHub → `/auth/github/callback` → engine
4. Engine resolves the account, either creating a new user or
   linking the GitHub identity to an existing email (the engine sets
   `associate_by_email=True`, so a user who signed up with
   `alice@example.com` can later log in via the GitHub account that
   also returns `alice@example.com` — same row, no duplicate).
5. Engine → `DAP_AUTH_OAUTH_REDIRECT_URL?token=...` → dashboard
6. Dashboard sets the cookie → user lands on `/`.

---

## Google OAuth

### One-time setup

1. Go to **Google Cloud Console → APIs & Services → Credentials**.
2. **Configure OAuth consent screen** if you haven't (`External`
   user type for general teams).
3. **Create credentials → OAuth client ID**:
   - **Application type**: Web application.
   - **Authorised JavaScript origins**: `https://<your-dashboard>`.
   - **Authorised redirect URIs**:
     `https://<your-engine>/auth/google/callback`.
4. Copy the client ID + client secret.

Scopes requested: Google's defaults (`openid email profile`) — enough
to populate the user account.

### Engine config

```bash
# .env
DAP_OAUTH_GOOGLE_CLIENT_ID=<client-id>.apps.googleusercontent.com
DAP_OAUTH_GOOGLE_CLIENT_SECRET=<client-secret>
DAP_AUTH_OAUTH_REDIRECT_URL=https://dap.example.com/api/auth/oauth/callback
```

Note that `DAP_AUTH_OAUTH_REDIRECT_URL` is shared between GitHub and
Google — the dashboard route handler is provider-agnostic.

### Login flow

Same shape as GitHub, swapping the provider router prefix to
`/auth/google`. Google enforces verified emails server-side so the
engine flags Google-created users as `is_verified=True`
automatically.

---

## API tokens

Opaque `dap_<urlsafe>` tokens for CLIs and scripts. Each token is
returned **once at creation** and stored SHA-256-hashed in the DB —
the plaintext is never recoverable. Revocation is immediate.

### Create

From the dashboard: `/admin/api-tokens` (admin) or your
`/settings/api-tokens` (user-self, lands in v0.4).

From the API (JWT-only — API tokens can't mint other tokens):

```bash
# 1. Log in to get a JWT.
JWT=$(curl -s http://localhost:7333/auth/jwt/login \
    -d 'username=alice@example.com&password=hunter12345' \
    | jq -r .access_token)

# 2. Mint a token.
curl -X POST http://localhost:7333/auth/api-tokens \
    -H "authorization: bearer $JWT" \
    -H 'content-type: application/json' \
    -d '{"name":"my-ci-bot","expires_in_days":90}'
# →
# {
#   "id": "...",
#   "name": "my-ci-bot",
#   "prefix": "dap_abc1",
#   "created_at": "...",
#   "expires_at": "...",
#   "last_used_at": null,
#   "revoked_at": null,
#   "token": "dap_abc12345...full_value...xyz"   ← only returned now
# }
```

The `name` is a label for revocation later — make it descriptive
(`ci-bot-staging`, `backup-script-2026`). `expires_in_days` is
optional (1–3650); omit it for tokens that don't auto-expire.

### Use

Attach as a `Bearer` token, same as JWT:

```bash
curl http://localhost:7333/runs \
    -H 'authorization: bearer dap_abc12345...'
```

`dap` CLI integration (`dap auth login --token`) lands in v0.4. For
v0.3 either set the header manually in a wrapper script or use
JWT-based login.

### List

`GET /auth/api-tokens` returns tokens you own (each carries the
`prefix` field for identification, never the full value). Admins
see every token across users via `GET /auth/api-tokens/admin`. The
dashboard navigates to this surface at `/admin/api-tokens` — that
URL is a *frontend route*, not an engine endpoint.

### Revoke

`DELETE /auth/api-tokens/{id}` (self) or `DELETE
/auth/api-tokens/admin/{id}` (admin-wide). Both fire an audit
event; subsequent requests with that token return 401. The
dashboard surfaces both flows under `/admin/api-tokens`.

---

## Where to look when things break

| Symptom | Look at |
|---|---|
| `401 Unauthorized` on every API call | Token expired (JWT) or revoked (API token). Re-login. |
| `400 Bad Request` on register | Password under 8 chars, or email already exists. |
| OAuth flow lands on JSON page, not dashboard | `DAP_AUTH_OAUTH_REDIRECT_URL` unset. |
| OAuth callback returns 404 | Provider's callback URL doesn't match `<your-engine>/auth/<provider>/callback`. |
| Multi-replica deploy: random logouts | `DAP_AUTH_JWT_SECRET` not pinned across replicas — each is signing with its own random. |
| `forgot-password` succeeds but no email | No SMTP integration in v0.3. Use admin-driven reset or `DAP_AUTH_LOG_RESET_TOKENS=1` for dev. |
| User signed up via OAuth but can't password-login | Their row has no password set. Admin resets it from `/admin/users`. |

See also:

- [`self-hosting.md`](self-hosting.md) — env vars, deployment paths.
- [`admin-guide.md`](admin-guide.md) — `/admin/users` operator UI.
- [`security.md`](security.md) — secrets handling, rotation, threat
  model.
