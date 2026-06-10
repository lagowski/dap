"""Engine configuration — per-subsystem dataclasses + ``EngineConfig`` (#778 Phase 2).

Moved out of ``app.py`` so the FastAPI wiring and the config surface
live in separate modules. ``app.py`` re-exports every public name below,
keeping the long-standing ``from dap_engine.app import EngineConfig``
import path working; new code should import from ``dap_engine.config``.

Env-var binding deliberately stays in ``__main__.py`` — these classes
are env-agnostic so tests and the CLI can construct them directly.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, NotRequired, TypedDict, Unpack
from urllib.parse import urlparse

__all__ = [
    "DEFAULT_CORS_ORIGINS",
    "AuthConfig",
    "CryptoConfig",
    "DatabaseConfig",
    "EngineConfig",
    "EngineConfigKwargs",
    "OAuthConfig",
    "RuntimePolicyConfig",
    "ServerConfig",
    "TemplateRegistryConfig",
    "parse_cors_origins",
]

# Local-dev defaults: dashboard on :3000, alt port :7332. Override at
# deploy time via ``EngineConfig.cors_origins`` or the ``DAP_CORS_ORIGINS``
# env var (#259).
DEFAULT_CORS_ORIGINS: list[str] = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:7332",
    "http://127.0.0.1:7332",
]


def parse_cors_origins(raw: str | None) -> list[str] | None:
    """Parse ``DAP_CORS_ORIGINS`` (comma-separated origins) into a list.

    Returns ``None`` when the env var is unset or empty after stripping —
    callers treat that as "use the default local-dev list". Whitespace
    around individual entries is trimmed; empty entries are dropped so a
    trailing comma doesn't produce a bogus origin string.
    """
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",")]
    cleaned = [p for p in parts if p]
    # Fail startup on malformed entries (#778 audit security #5) — a
    # typo'd origin (missing scheme, bare host) would never match in the
    # browser and silently break the dashboard. ``*`` stays allowed as an
    # explicit operator choice.
    for origin in cleaned:
        if origin == "*":
            continue
        parsed = urlparse(origin)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            # Origin headers never carry a path/query/fragment — an entry
            # with one (even a bare trailing slash) would silently never
            # match CORSMiddleware's exact string comparison.
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                f"DAP_CORS_ORIGINS entry {origin!r} is not a valid origin — "
                "expected http(s)://host[:port] with no trailing path, or '*'"
            )
    return cleaned or None


# ---------------------------------------------------------------------------
# Engine config — nested groups + flat-kwarg compatibility (audit E8)
# ---------------------------------------------------------------------------
#
# The old 28-field flat ``@dataclass`` mixed db / server / auth / oauth /
# template-registry / crypto concerns in one record (audit complaint:
# "will keep growing, hard to read"). The five sub-dataclasses below
# group fields by subsystem so the definition reads like a config
# table-of-contents rather than a wall of options.
#
# Back-compat: ``EngineConfig.__init__`` accepts BOTH old-style flat
# kwargs (e.g. ``EngineConfig(db_path="...", auth_jwt_secret="...")``,
# what every test fixture uses today) AND new-style nested-instance
# kwargs (``EngineConfig(db=DatabaseConfig(path="..."))``). The
# ``__getattr__`` fallback below maps the legacy flat attribute
# names to their nested locations, so production code reading
# ``cfg.auth_jwt_secret`` keeps working during the gradual migration
# to ``cfg.auth.jwt_secret``.


@dataclass
class DatabaseConfig:
    """Database / storage knobs.

    ``database_url`` takes precedence over ``db_path`` when both are
    set. Prefix determines dialect (``sqlite+aiosqlite://…`` for
    SQLite, ``postgresql+asyncpg://…`` for PostgreSQL).
    """

    db_path: str = "./.dap/state.db"
    database_url: str | None = None
    # PostgreSQL checkpointer pool sizing (#187, #238).
    # AsyncPostgresSaver serialises checkpoint ops behind a Lock, so
    # only 1 connection is in flight at a time — max_size governs
    # burst concurrency across simultaneous runs, not within one.
    # min_size=4 matches the psycopg_pool default and keeps enough
    # warm connections for the gate checkpoint write that arrives
    # after a long idle Phase 1.
    pg_pool_min_size: int = 4
    pg_pool_max_size: int = 10
    # Seconds the pool retries connecting after a server-side drop (#580).
    # Without this the pool discards stale connections but does not retry
    # within the same request — the run fails instantly.
    pg_pool_reconnect_timeout: float = 300.0


@dataclass
class ServerConfig:
    """HTTP server + per-request limits."""

    host: str = "127.0.0.1"
    port: int = 7333
    # CORS origins permitted on the engine's REST API (#259). ``None``
    # means "fall back to ``DEFAULT_CORS_ORIGINS``" — the local-dev
    # list. Production deployments override via env var
    # ``DAP_CORS_ORIGINS`` or by passing ``cors_origins=[...]`` here.
    cors_origins: list[str] | None = None
    # Hard cap for ``POST /agents/dry-run`` (#103). Each invocation
    # pays real LLM tokens, so we refuse calls whose agent
    # ``budget_limit_usd`` exceeds this. Belt-and-suspenders against
    # a runaway form value or a forgotten zero default in the UI.
    dry_run_budget_usd: float = 0.50


@dataclass
class AuthConfig:
    """JWT / password authentication settings.

    ``jwt_secret`` is required for any auth-protected route. Tests
    set a deterministic value; production reads ``DAP_AUTH_JWT_SECRET``
    in ``__main__`` and refuses to start with a default. We accept
    ``None`` here only to keep the dataclass default-constructible;
    the lifespan generates a per-process random secret in that case
    (sufficient for local dev where every restart invalidates
    outstanding tokens).
    """

    # ``repr=False`` — never expose the JWT secret in tracebacks /
    # debug prints / Sentry payloads. ``__repr__`` of ``AuthConfig``
    # will show ``AuthConfig(access_ttl_seconds=900, log_reset_tokens
    # =False)`` instead of leaking the actual secret.
    jwt_secret: str | None = field(default=None, repr=False)
    access_ttl_seconds: int = 60 * 15
    # Password-reset token logging (sub-B3 review).
    #
    # When ``True``, the ``UserManager.on_after_forgot_password``
    # hook logs the raw reset token at WARNING level. Useful for
    # self-hosted dev / one-operator instances that have no email
    # delivery yet. **Off by default** because reset tokens are
    # credentials; production log aggregation would otherwise
    # routinely contain account-takeover material. Set via
    # ``DAP_AUTH_LOG_RESET_TOKENS``.
    log_reset_tokens: bool = False


@dataclass
class OAuthConfig:
    """GitHub + Google OAuth credentials + post-login redirect.

    Each provider is opt-in: when both client_id and client_secret
    are set the corresponding ``/auth/<provider>`` router is mounted;
    otherwise nothing is exposed for that provider. Self-host
    installs can run with email+password only and add OAuth later
    by setting these env vars and restarting.
    """

    # client_id values are visible in OAuth's authorize URL anyway —
    # not secrets, so safe in __repr__. ``client_secret`` values ARE
    # secrets; ``repr=False`` keeps them out of tracebacks.
    github_client_id: str | None = None
    github_client_secret: str | None = field(default=None, repr=False)
    google_client_id: str | None = None
    google_client_secret: str | None = field(default=None, repr=False)
    # Post-login redirect for OAuth callbacks (sub-B5). When set,
    # fastapi-users' OAuth callback redirects the browser to this URL
    # with the access token as a ``?token=<jwt>`` query parameter,
    # instead of returning JSON. The dashboard's
    # ``/api/auth/oauth/callback`` handler reads the token, sets the
    # httpOnly cookie, and lands the user on the home page.
    #
    # In dev this points at the dashboard's local URL
    # (``http://localhost:3000/api/auth/oauth/callback``); production
    # operators set ``DAP_AUTH_OAUTH_REDIRECT_URL`` to the equivalent
    # dashboard URL. When ``None`` (no dashboard wired) the callback
    # falls back to returning JSON — useful for CLI-only deployments.
    redirect_url: str | None = None


@dataclass
class TemplateRegistryConfig:
    """Trusted-source allowlist for ``/pipelines/import-from-url`` (#385).

    When ``allowed_hosts`` is empty (default), the endpoint returns
    422 — the feature is opt-in. Operators populate the list with
    literal hostnames (no wildcards) of trusted bundle sources; the
    endpoint rejects URLs whose hostname isn't an exact match.

    ``auth_token``: optional Bearer token sent on every fetch. Common
    case: a fine-grained GitHub PAT scoped to one private bundle
    repo. Per-host tokens land in a follow-up ticket.
    """

    allowed_hosts: list[str] = field(default_factory=list)
    # Bearer token — same secret-handling policy as JWT / OAuth secrets.
    auth_token: str | None = field(default=None, repr=False)


@dataclass
class CryptoConfig:
    """At-rest encryption keys.

    ``instance_env_vars_key`` — Fernet key for instance env-var values
    (#388). Set via ``DAP_INSTANCE_ENV_VARS_KEY``. When ``None``, the
    ``/settings/admin/env-vars`` POST endpoint returns 503 so an
    operator notices the misconfiguration immediately instead of
    writing rows that can't be decrypted on the next restart. DELETE
    and GET work without the key: removing an undecryptable row is a
    legitimate cleanup path after a botched rotation, and the masked
    preview shown on GET is stored unencrypted alongside the
    ciphertext.
    """

    # Fernet key — secret, kept out of __repr__.
    instance_env_vars_key: str | None = field(default=None, repr=False)


@dataclass
class RuntimePolicyConfig:
    """Policy gates for runtimes that execute host-local code."""

    # Secure default for multi-user installs: only admins may run bash.
    # Single-user/local-trust deployments can opt in explicitly via
    # DAP_ALLOW_BASH_RUNTIME_FOR_NON_ADMIN=1.
    allow_bash_runtime_for_non_admin: bool = False


# Maps every legacy flat field name → ``(group, nested_name)``. Used
# by ``EngineConfig.__init__`` to route flat kwargs into the right
# nested group, and by ``__getattr__`` to translate legacy attribute
# reads. When you add a new field to a nested group, add a row here
# only if you also want legacy flat-style access — new fields with no
# pre-existing flat name don't need an entry.
_FLAT_TO_NESTED: dict[str, tuple[str, str]] = {
    # DatabaseConfig
    "db_path": ("db", "db_path"),
    "database_url": ("db", "database_url"),
    "pg_pool_min_size": ("db", "pg_pool_min_size"),
    "pg_pool_max_size": ("db", "pg_pool_max_size"),
    "pg_pool_reconnect_timeout": ("db", "pg_pool_reconnect_timeout"),
    # ServerConfig
    "host": ("server", "host"),
    "port": ("server", "port"),
    "cors_origins": ("server", "cors_origins"),
    "dry_run_budget_usd": ("server", "dry_run_budget_usd"),
    # AuthConfig
    "auth_jwt_secret": ("auth", "jwt_secret"),
    "auth_access_ttl_seconds": ("auth", "access_ttl_seconds"),
    "auth_log_reset_tokens": ("auth", "log_reset_tokens"),
    # OAuthConfig
    "oauth_github_client_id": ("oauth", "github_client_id"),
    "oauth_github_client_secret": ("oauth", "github_client_secret"),
    "oauth_google_client_id": ("oauth", "google_client_id"),
    "oauth_google_client_secret": ("oauth", "google_client_secret"),
    "auth_oauth_redirect_url": ("oauth", "redirect_url"),
    # TemplateRegistryConfig
    "template_registry_allowed_hosts": ("template_registry", "allowed_hosts"),
    "template_registry_auth_token": ("template_registry", "auth_token"),
    # CryptoConfig
    "instance_env_vars_key": ("crypto", "instance_env_vars_key"),
    # RuntimePolicyConfig
    "allow_bash_runtime_for_non_admin": ("runtime_policy", "allow_bash_runtime_for_non_admin"),
}

_NESTED_GROUP_NAMES = frozenset(
    {"db", "server", "auth", "oauth", "template_registry", "crypto", "runtime_policy"}
)


class EngineConfigKwargs(TypedDict, total=False):
    """Typed kwargs surface for ``EngineConfig.__init__``.

    Lets mypy catch typos and type errors that ``**kwargs: Any`` would
    silently allow. Every entry is ``NotRequired`` because each is
    optional with a sensible default in the corresponding nested
    dataclass.

    Both kwarg styles share this dict — ``db`` accepts a full
    ``DatabaseConfig`` instance, the flat-style fields below it accept
    their scalar types. ``EngineConfig.__init__`` enforces that the
    nested-instance kwargs (``db``, ``auth``, etc.) are not combined
    with their flat-style counterparts via the
    ``_assert_no_double_specification`` check, so mypy doesn't need
    to model that mutual-exclusion.
    """

    # Nested-instance kwargs (preferred for new code)
    db: NotRequired[DatabaseConfig]
    server: NotRequired[ServerConfig]
    auth: NotRequired[AuthConfig]
    oauth: NotRequired[OAuthConfig]
    template_registry: NotRequired[TemplateRegistryConfig]
    crypto: NotRequired[CryptoConfig]
    runtime_policy: NotRequired[RuntimePolicyConfig]
    # Flat-style kwargs (legacy — every test fixture uses these)
    db_path: NotRequired[str]
    database_url: NotRequired[str | None]
    pg_pool_min_size: NotRequired[int]
    pg_pool_max_size: NotRequired[int]
    pg_pool_reconnect_timeout: NotRequired[float]
    host: NotRequired[str]
    port: NotRequired[int]
    cors_origins: NotRequired[list[str] | None]
    dry_run_budget_usd: NotRequired[float]
    auth_jwt_secret: NotRequired[str | None]
    auth_access_ttl_seconds: NotRequired[int]
    auth_log_reset_tokens: NotRequired[bool]
    oauth_github_client_id: NotRequired[str | None]
    oauth_github_client_secret: NotRequired[str | None]
    oauth_google_client_id: NotRequired[str | None]
    oauth_google_client_secret: NotRequired[str | None]
    auth_oauth_redirect_url: NotRequired[str | None]
    template_registry_allowed_hosts: NotRequired[list[str]]
    template_registry_auth_token: NotRequired[str | None]
    instance_env_vars_key: NotRequired[str | None]
    allow_bash_runtime_for_non_admin: NotRequired[bool]


@dataclass(init=False)
class EngineConfig:
    """Top-level engine config — aggregates the per-subsystem groups.

    Construct with **either** flat kwargs (legacy style — every test
    fixture in the repo uses this) **or** nested-instance kwargs
    (preferred for new code, e.g. ``EngineConfig(auth=AuthConfig(
    jwt_secret="..."))``). Mix-and-match is also fine: a missing
    group default-factories to its empty form.

    Reading: prefer nested form in new code (``cfg.auth.jwt_secret``).
    Legacy flat reads (``cfg.auth_jwt_secret``) are translated via
    ``__getattr__`` to the nested storage so existing production code
    keeps working during the migration; ruff's ``PLW1641`` will start
    flagging legacy reads once a future ``@deprecated`` decorator
    lands.
    """

    db: DatabaseConfig
    server: ServerConfig
    auth: AuthConfig
    oauth: OAuthConfig
    template_registry: TemplateRegistryConfig
    crypto: CryptoConfig
    runtime_policy: RuntimePolicyConfig

    def __init__(self, **kwargs: Unpack[EngineConfigKwargs]) -> None:
        # ``copy.copy`` defends against the ``dataclasses.replace``
        # contract: ``replace(cfg, foo="x")`` passes the existing
        # nested instances back to ``__init__`` along with the new
        # flat kwarg. Without the copy, the flat-kwarg setattr would
        # mutate the original cfg's nested object — leaking new state
        # into the "old" config and breaking caller assumptions about
        # ``replace`` returning a fresh instance.
        #
        # ``is None`` (not ``or``) so a falsy-but-valid nested
        # instance (e.g., a user-built ``DatabaseConfig()`` with all
        # defaults — bool() of any dataclass is True today but the
        # idiom shouldn't depend on that) survives the fallback.
        db = kwargs.pop("db", None)
        nested: dict[str, Any] = {
            "db": copy.copy(db) if db is not None else DatabaseConfig(),
        }
        for group_name, default_cls in (
            ("server", ServerConfig),
            ("auth", AuthConfig),
            ("oauth", OAuthConfig),
            ("template_registry", TemplateRegistryConfig),
            ("crypto", CryptoConfig),
            ("runtime_policy", RuntimePolicyConfig),
        ):
            value = kwargs.pop(group_name, None)  # type: ignore[misc]
            nested[group_name] = copy.copy(value) if value is not None else default_cls()

        # Now sort remaining flat kwargs into their groups.
        for flat_name in list(kwargs.keys()):
            mapping = _FLAT_TO_NESTED.get(flat_name)
            if mapping is None:
                raise TypeError(
                    f"EngineConfig got an unexpected keyword argument {flat_name!r}",
                )
            group, nested_name = mapping
            # Mutate the COPY, not the caller's original.
            setattr(nested[group], nested_name, kwargs.pop(flat_name))  # type: ignore[misc]

        # Bypass our own ``__setattr__`` (which would loop) by going
        # through ``object.__setattr__``. Each group field name is in
        # ``_NESTED_GROUP_NAMES`` so the setattr-router would route it
        # back here harmlessly, but skipping the indirection is
        # clearer.
        for group_name, value in nested.items():
            object.__setattr__(self, group_name, value)

    def __getattr__(self, name: str) -> Any:
        """Translate legacy flat-attribute reads to their nested home.

        Only called when normal attribute lookup fails (i.e. when the
        caller used a legacy name that isn't a real ``EngineConfig``
        attribute). Production code can migrate to
        ``cfg.section.field`` at its own pace without breaking older
        callsites. New attribute names that aren't in
        ``_FLAT_TO_NESTED`` still raise ``AttributeError`` so typos
        fail loud.
        """
        mapping = _FLAT_TO_NESTED.get(name)
        if mapping is None:
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {name!r}",
            )
        group, nested_name = mapping
        return getattr(getattr(self, group), nested_name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Route legacy flat-attribute writes to their nested home.

        Without this, ``cfg.auth_jwt_secret = "new"`` would create an
        instance attribute on ``EngineConfig`` itself that diverges
        from the nested storage — migrated code reading
        ``cfg.auth.jwt_secret`` would see the old value, unmigrated
        code reading ``cfg.auth_jwt_secret`` would see the new one.
        Routing the write keeps the two surfaces in sync.

        Nested-group field names (``db``, ``server``, etc.) go through
        ``object.__setattr__`` directly so the initial ``__init__``
        wiring doesn't recurse. Unknown names also use the direct
        path — Python's dataclass machinery (and tests that monkey-
        patch attributes) need that escape hatch.
        """
        if name in _FLAT_TO_NESTED:
            group, nested_name = _FLAT_TO_NESTED[name]
            # ``object.__getattribute__`` (not the bare ``getattr``)
            # to retrieve the nested instance — bypasses our own
            # ``__getattr__`` fallback so a future entry in
            # ``_FLAT_TO_NESTED`` that accidentally collides with a
            # group name (``auth``, ``oauth``, etc.) can't loop into
            # ``__getattr__`` and recurse here. The collision is
            # impossible today (the keys are all flat aliases) but
            # the explicit bypass is defense-in-depth.
            nested_instance = object.__getattribute__(self, group)
            setattr(nested_instance, nested_name, value)
        else:
            object.__setattr__(self, name, value)
