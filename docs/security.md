# Security posture

DAP v0.3 is a self-hosted multi-user instance for small teams (5–20
people). This document is an honest read of what it does for you
and — equally important — what it deliberately doesn't, so an
operator can make informed decisions about deployment hardening.

If you're a security researcher and you've found something not
covered here, please open a private security advisory on GitHub.

## Threat model in one sentence

DAP protects user A's pipelines / agents / runs from being seen
or modified by user B on the same instance. It does **not** stand
between you and a determined attacker with shell access, nor does
it replace a reverse proxy with TLS termination + rate limiting
in front of the engine.

## Secrets handling

### `.env.local` is gitignored

The repo's `.gitignore` covers exactly three patterns: `.env`,
`.env.local`, and `.env.*.local` (so `.env.production.local`,
`.env.staging.local`, etc. are all safe). Other `.env.*` shapes
(e.g. `.env.production` without `.local`) are **not** ignored —
if you stash secrets in one of those, you'll commit them. Stick to
the `.env.local` convention or extend `.gitignore` first. Real
secrets — JWT key, OAuth client secrets, provider API keys — live
in `.env.local` (dev) or the deployment's secret manager (prod).
The pre-push hook from `scripts/setup` blocks accidental pushes to
`main` / `develop`, but it doesn't grep diffs for secrets; if you
commit a credential by accident, **rotate immediately** — git
history is permanent.

### `DAP_AUTH_JWT_SECRET`

Signs the dashboard's cookie session (`dap-jwt`, httpOnly). When
unset the engine generates a per-process random — fine for local
dev (logged-in users get logged out on every restart), broken in
multi-replica deploys (each replica is signing with its own key).

Note: the standalone **Docker entrypoint** (`docker/entrypoint.sh`)
fails fast with exit code 64 if `DAP_AUTH_JWT_SECRET` is missing,
so the per-process-random fallback only applies to source / pipx
installs. Compose deployments either set the secret or refuse to
start.

**Rotation procedure:**

```bash
NEW=$(openssl rand -hex 32)
# Replace it in .env / secret manager, restart engine.
```

Rotation invalidates every outstanding cookie session — every
user gets logged out, no rolling-rotation support in v0.3. API
tokens are NOT signed with this secret (see below), so they keep
working.

Key size: 32+ bytes from a CSPRNG. Don't use a passphrase. Don't
reuse the secret across environments.

### Provider API keys

`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
`GLM_API_KEY` and friends. Engine reads them at startup, passes
them to runtime adapters, and never logs them. Recommended scope
minimisation:

- Anthropic / OpenAI / Google: create dedicated keys for each
  DAP deployment so you can revoke one without blast-radius
  across others.
- Set per-key spend limits at the provider level — engine
  enforces `dry_run_budget_usd` for the dashboard's Test panel,
  but it can't bound production `/runs` calls.

## Password storage

### Argon2id via pwdlib

`PasswordHelper` (fastapi-users default → `pwdlib`) writes
Argon2id hashes shaped like
`$argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>` — 64 MiB memory
cost, 3 iterations, 4 parallel lanes. RFC 9106 baseline.

### Password policy

8+ character minimum, enforced by `UserManager.validate_password`.
**That's it.** No complexity rules, no breach-database lookups, no
periodic forced rotation. Rationale: NIST 800-63B retired
complexity rules as counterproductive years ago, and the engine
defers password quality to upstream policies (browser password
manager, SSO via OAuth, etc.).

If you want stronger guarantees, prefer OAuth (GitHub / Google) or
API tokens (which are operator-generated and high-entropy by
construction). The password authenticator is the "still need
something" baseline.

## JWT signing

### HS256 with HMAC

Tokens are short-lived (default `DAP_AUTH_ACCESS_TTL_SECONDS=900`,
i.e. 15 min), HS256-signed against `DAP_AUTH_JWT_SECRET`. There
is **no refresh-token flow** — the short TTL bounds revocation
latency without one. When a user logs out the dashboard clears
the cookie; the JWT itself stays valid until expiry. Re-using it
against the engine inside that 15-min window would succeed.

### Implications

- Token theft (XSS, leaked cookie) → ~15-minute compromise window.
- JWT rotation invalidates every session immediately (no DB-side
  state to update).
- Mass-logout for incident response: rotate the secret.

### What HS256 does not protect against

- Browser-side attacks. The dashboard sets the cookie httpOnly so
  JavaScript can't read it, but XSS on a custom page rendered
  alongside the dashboard would defeat that. Don't co-host the
  dashboard with untrusted content on the same origin.
- A compromised server. If an attacker has the secret, they can
  forge tokens — this is by design (symmetric signing), not a
  weakness in DAP specifically.

## API tokens

### Format

`dap_<43-base64url-chars>` — 32 random bytes from `secrets.token_urlsafe`
(≈ 256 bits of entropy). The `dap_` prefix lets log filters and
git-secret scanners catch them in plaintext.

The first 8 chars after `dap_` are split off and stored in the
indexed `token_prefix` DB column (so we can look up the candidate
row without table-scanning), and the entire raw token is
SHA-256-hashed into `token_hash`. The full token is **never
stored** — operators who lose it must mint a new one. The
public-facing field on `ApiTokenRead` is named `prefix` (renamed
from the underlying column for API ergonomics); manual SQL
queries should use the actual `token_prefix` column name.

### Validation

```python
candidate = hashlib.sha256(raw.encode("utf-8")).hexdigest()
hmac.compare_digest(token.token_hash, candidate)
```

Constant-time compare against the row located by `token_prefix`.

### Revocation

Setting `revoked_at` on the row → subsequent requests with that
token return 401. The token row stays in `api_tokens` for audit;
it just stops authenticating. Admin-wide revoke via
`DELETE /auth/api-tokens/admin/{id}`; self-revoke via
`DELETE /auth/api-tokens/{id}`.

### Lifetime

No expiry by default — long-lived. Operators with stricter
policies pass `expires_in_days` (1–3650) at mint time; the engine
rejects expired tokens with 401. The audit trail (`api_token.created`
+ `api_token.revoked`) is the auditing surface — there's no
periodic-rotation reminder in v0.3.

### Important: API tokens don't use the JWT secret

Rotating `DAP_AUTH_JWT_SECRET` does **not** invalidate API
tokens. They authenticate via the SHA-256 lookup chain above —
completely separate code path. If your compromise scope includes
tokens, revoke them explicitly from `/admin/api-tokens` after
the JWT rotation.

## Soft-delete semantics

### `deleted_at NOT NULL` = "user removed"

Soft-deleted users are hidden from default `/admin/users` listings
(filter `include_deleted=true` to see them) and from non-admin
queries entirely. Their owned resources remain attached to the
deleted-user row — visible to admins for audit purposes,
invisible to other users.

### Hard delete

Out of scope as a managed surface in v0.3. The schema cascades
cleanly when you DELETE the row:

- `pipelines` / `agents` / `projects` / `runs` / `api_tokens` /
  `oauth_accounts` all have `ON DELETE CASCADE` on their
  `user_id` FK.
- `audit_log` has no FK to `users` (audit integrity > referential
  cleanliness). Its rows survive the hard delete with their
  original `user_id` value.

### GDPR / right-to-erasure

Hard-delete the user row (cascade handles ownership). The
audit_log entries with their `user_id` survive — that's
intentional (audit completeness), and the values are opaque UUIDs,
not PII. If the controller decides audit_log entries themselves
must be erased, `DELETE FROM audit_log WHERE user_id = ...`
against the DB does it. A managed UI for both lands in v0.4.

## Network exposure

### Plain HTTP on the wire

Engine listens on `:7333`, dashboard on `:3000` (Docker) or
`:7332` (pipx). Both speak plain HTTP — the engine never
terminates TLS itself. **Production deployments MUST sit behind
a reverse proxy that terminates TLS.**

### Reverse proxy responsibilities

| Concern | Where |
|---|---|
| TLS termination | Reverse proxy |
| HTTP → HTTPS redirect | Reverse proxy |
| Brute-force / rate limiting | Reverse proxy |
| HSTS, security headers | Reverse proxy |
| WAF / IP allowlists | Reverse proxy |
| `dap-jwt` cookie set / read | Dashboard (Next.js `/api/auth/*` route handlers) |
| `Authorization: Bearer` header → JWT validation | Engine |
| Authorization (ownership filters, admin role) | Engine |
| CORS allow-list | Engine (`DAP_CORS_ORIGINS`) |

### Same-origin proxy

The dashboard exposes a catch-all `/api/[...path]` Next.js route
handler that forwards engine calls server-side. This means:

- Only the dashboard port needs to be public.
- The engine port can stay bound to `127.0.0.1` (or a private
  Docker network) in production.
- The browser only ever sees the dashboard origin — no
  third-party cookies, no CORS surprises.

### Example: Caddy with auto-HTTPS + rate limiting

```caddy
dap.example.com {
    # Auto-provisions Let's Encrypt cert. ACME endpoint default.
    encode gzip

    # Rate limit the auth endpoints. The rate-limit module isn't
    # in the default Caddy build — install via xcaddy or use the
    # community Docker image with `caddy-rate-limit` baked in.
    @auth path /auth/jwt/login /auth/forgot-password /auth/register
    rate_limit @auth {
        zone auth_zone {
            key {remote_ip}
            events 10
            window 1m
        }
    }

    # Forward everything to the dashboard. The dashboard's
    # /api/[...path] handler forwards to the engine internally.
    reverse_proxy dashboard:3000
}
```

### Example: nginx (no auto-TLS, you handle certs)

```nginx
server {
    listen 443 ssl http2;
    server_name dap.example.com;

    ssl_certificate     /etc/letsencrypt/live/dap.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/dap.example.com/privkey.pem;

    # Brute-force protection on the auth surface.
    limit_req_zone $binary_remote_addr zone=dap_auth:10m rate=10r/m;

    location /auth/ {
        limit_req zone=dap_auth burst=5 nodelay;
        proxy_pass http://dashboard:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
    }

    location / {
        proxy_pass http://dashboard:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
}
```

## What v0.3 deliberately does NOT do

These are conscious non-goals — open issues exist for some,
others are off-roadmap entirely. Document them clearly so
operators don't assume coverage that isn't there.

| Missing capability | Where to compensate |
|---|---|
| Rate limiting on the auth endpoints | Reverse proxy (`Caddy rate_limit`, `nginx limit_req`, `Cloudflare`). |
| Account lockout after N failed logins | Reverse proxy fail2ban-style banning, or operator monitoring `user.logged_in` audit events. |
| Per-resource ACLs / team boundaries / orgs | Off-roadmap (would shift DAP into SaaS multi-tenancy). Either trust everyone in your instance or run one instance per team. |
| 2FA / TOTP / WebAuthn | Off-roadmap for v0.3. Closest mitigation: OAuth via GitHub / Google, which enforces 2FA at the provider. |
| SSO via SAML / OIDC | Off-roadmap. GitHub / Google OAuth is the closest analogue. |
| SCIM provisioning | Off-roadmap. Manual `/admin/users` for now. |
| Refresh tokens | Deliberate omission — 15-min JWT TTL bounds revocation latency without the extra surface. |
| Encrypted-at-rest database | Filesystem encryption (LUKS, FileVault) or a cloud DB with at-rest encryption. The SQLite file is unencrypted on disk. |
| Encrypted secrets at rest | Provider API keys live in env vars. Use the deployment's secret manager (Docker Swarm secrets, K8s `Secret`, Vault, AWS Secrets Manager). |
| Periodic password rotation | Not enforced — NIST 800-63B retired that practice. Operators with compliance requirements set it via policy + admin nudges. |

## Hardening checklist for production

In rough order of impact:

- [ ] Reverse proxy with TLS termination (Caddy / nginx / Traefik).
- [ ] `DAP_AUTH_JWT_SECRET` set to 32+ bytes from `openssl rand`.
- [ ] `DAP_CORS_ORIGINS` set to your dashboard origin(s).
- [ ] Rate limiting on `/auth/jwt/login`, `/auth/forgot-password`,
      `/auth/register` (reverse proxy).
- [ ] OAuth instead of password where possible (delegates 2FA to
      the provider).
- [ ] `DAP_AUTH_LOG_RESET_TOKENS=0` or unset (default).
- [ ] Postgres instead of SQLite for >1 user (concurrent
      writes + better backup story).
- [ ] Provider API keys scoped to DAP only (revocable
      independently).
- [ ] Filesystem encryption on the host (LUKS / FileVault).
- [ ] Audit log monitoring — `user.logged_in` failures, bursts of
      `api_token.created`, unexpected `user.password_reset`.
- [ ] Backup automation for the DB (SQLite: `.backup`; Postgres:
      `pg_dump` cron). Test restore quarterly.
- [ ] Pin the Docker image tag (`ghcr.io/rafeekpro/dap:0.3.0`,
      not `latest`) so upgrades are intentional.

## See also

- [`self-hosting.md`](self-hosting.md) — deployment, env vars,
  TLS reverse-proxy templates.
- [`auth.md`](auth.md) — credential mechanisms in detail.
- [`admin-guide.md`](admin-guide.md) — operator UI walkthrough,
  recovery procedures.

## Reporting

Found a vulnerability? Please file a **GitHub Security Advisory**
on `rafeekpro/dap` (not a public issue) so we can triage and
ship a fix before the details land in the open tracker.
