"""SQLAlchemy 2.0 ORM models.

Versioning model:
- `agents` and `pipelines` hold stable identity and a pointer to the current version.
- `agent_versions` and `pipeline_versions` hold the full immutable configuration per
  version. Updating an agent/pipeline always creates a new version row; previous
  versions remain unchanged so runs can reference exact configurations forever.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi_users.db import SQLAlchemyBaseOAuthAccountTableUUID, SQLAlchemyBaseUserTableUUID
from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Users (auth — v0.3, see #299)
# ---------------------------------------------------------------------------


class UserORM(SQLAlchemyBaseUserTableUUID, Base):
    """Authenticated user.

    Inherits the ``id`` (UUID), ``email`` (unique), ``hashed_password``,
    ``is_active``, ``is_superuser``, ``is_verified`` columns from
    ``SQLAlchemyBaseUserTableUUID``. We extend with audit timestamps and
    a soft-delete column.

    Naming note: the fastapi-users default ``__tablename__`` is ``user``
    (singular). We override to ``users`` to match the rest of the schema
    (``agents``, ``pipelines``, ``runs``, ``projects``, …).

    Soft-delete: the ``UserManager.delete`` override (in
    ``auth/users.py``) writes ``deleted_at`` + flips ``is_active=False``
    instead of issuing ``DELETE FROM users``. This keeps the row alive
    so any future FK pointing at the user — e.g. the ``user_id`` column
    that lands on ``agents`` / ``pipelines`` / ``projects`` / ``runs`` in
    a follow-up sub-PR — remains valid without cascading data loss.
    Hard delete (admin-initiated, GDPR right-to-erasure) is exposed
    separately in Phase C/E.
    """

    __tablename__ = "users"

    # `default` runs Python-side on ORM `User(...)` construction (which is
    # how fastapi-users creates rows). `onupdate` keeps `updated_at` fresh
    # on subsequent UPDATEs without callers having to remember.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Linked OAuth identities. Eager-loaded ("joined") because fastapi-users'
    # SQLAlchemyUserDatabase reads this list on every login lookup; lazy
    # loading would emit a separate SELECT per request.
    oauth_accounts: Mapped[list[OAuthAccountORM]] = relationship(
        "OAuthAccountORM",
        lazy="joined",
        cascade="all, delete-orphan",
    )


class ApiTokenORM(Base):
    """Long-lived API token for CLI / scripts (#299, sub-A3).

    Tokens are opaque random strings (``dap_<43-char-base64>``) issued
    via ``POST /auth/api-tokens``. The raw token is shown to the caller
    exactly once at creation time; the database stores only the SHA-256
    hash and the first 8 chars (``token_prefix``) for indexed lookup.

    Why SHA-256 instead of bcrypt/argon2: the token has 256 bits of
    entropy, so brute-force is infeasible regardless of hash speed; a
    cryptographic but cheap hash keeps every authenticated request
    inexpensive. (Password hashes use slow KDFs because passwords have
    far less entropy and offline cracking is the realistic threat —
    that calculus doesn't apply here.)

    Lifecycle:
    - ``revoked_at`` set on ``DELETE /auth/api-tokens/{id}`` — the
      verify path treats any non-NULL value as "rejected"
    - ``expires_at`` optional. NULL means "no expiry"; a past timestamp
      is treated as revoked
    - ``last_used_at`` is touched on every successful auth so admins
      can spot stale tokens; not on the hot path under a tx (we update
      it best-effort and tolerate races)
    """

    __tablename__ = "api_tokens"
    __table_args__ = (
        # Lookup happens on every authenticated CLI call: select by
        # prefix (cheap, indexed), then SHA-256 verify the candidates.
        # Almost always one match per prefix; the verify step exists
        # purely to defeat collisions (very unlikely with 8 chars from a 64-
        # symbol alphabet) and to be timing-attack-resistant.
        Index("ix_api_tokens_token_prefix", "token_prefix"),
        Index("ix_api_tokens_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="cascade"),
        nullable=False,
    )
    # Human-readable label set by the user at create time
    # (e.g. "ci-pipeline", "personal-laptop").
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(8), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLogORM(Base):
    """Append-only audit trail of security-relevant events (#299, sub-A4).

    Captures the *what* + *who* + *when* of operator-visible auth /
    ownership events. ``event_type`` is a stable string key (e.g.
    ``user.registered``, ``user.logged_in``, ``api_token.created``,
    ``api_token.revoked``); ``event_data`` is a free-form JSON
    payload for per-event details (target user id, token prefix, IP
    address from the request, …).

    Lifecycle:
    - **Append-only**: rows are never updated or deleted by application
      code. A retention policy (e.g. monthly partition pruning) can
      reclaim space later if growth becomes a concern (Phase E).
    - ``user_id`` is nullable so events that happen *before* the actor
      is identified (e.g. failed login by an unknown email) can still
      be recorded.
    - No FK to ``users.id`` either — the row should survive even if
      the user account is hard-deleted (audit integrity > referential
      cleanliness).
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        # Admin filter UI lists by user, by event type, or by both. The
        # composite leads with user_id since "show me what user X did"
        # is the primary investigation flow; event_type alone falls back
        # to a small filtered scan.
        Index("ix_audit_log_user_event", "user_id", "event_type"),
        Index("ix_audit_log_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    # Nullable: pre-auth events (failed login by unknown email) and
    # system events (migration, scheduled cleanup) have no actor.
    user_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    # JSONB on PostgreSQL — supports native JSON path queries (the admin
    # panel will filter by ``event_data->>'token_id'`` etc.) and matches
    # the migration's CREATE TABLE column type. ``JSON.with_variant(JSONB,
    # "postgresql")`` keeps the SQLite path on plain JSON (TEXT under the
    # hood, no native operators but the column type is dialect-aware).
    event_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class InstanceEnvVarORM(Base):
    """Instance-level env var (#388).

    Shared key/value store owned by the operator. Values are encrypted
    at rest with Fernet (``dap_engine.auth.encryption``) — the column
    holds ciphertext only; plaintext lives in memory just long enough
    to merge into a subprocess env (see
    ``packages/runtimes/.../adapters/_subprocess_env.py``).

    Merge order at run start (rightmost wins on conflict):
    ``os.environ`` → ``instance_env_vars`` (this table) →
    ``ProjectORM.env_vars`` → per-agent ``runtime_config.env``.

    The intent is that operators define shared GitHub tokens, API
    keys, etc. once at the instance level and every project inherits
    them automatically at run time — not at project create time, so
    a token rotation here flows through to all existing projects on
    the next run.

    Why a dedicated table (not a generic ``settings`` blob):
    - One row per key keeps the audit log granular: every
      create/update/delete cites exactly one key.
    - Sets up cheap key lookups by ``UNIQUE(key)``.
    - Avoids serialising / deserialising the whole map on every write.
    """

    __tablename__ = "instance_env_vars"
    __table_args__ = (UniqueConstraint("key", name="uq_instance_env_vars_key"),)

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    # Validated at the API layer against ``^[A-Z_][A-Z0-9_]*$`` plus a
    # reserved-prefix denylist. The DB layer is intentionally permissive
    # so a future relaxation of the rules doesn't require a migration.
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    # Fernet ciphertext — url-safe base64, comfortably fits a TEXT
    # column. Plaintext never reaches the DB.
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    # First N chars of the *plaintext* value, captured at write time —
    # used by the masked GET listing. Storing it separately avoids
    # decrypting every row on every list call (and avoids a key being
    # required on read paths that only need the preview).
    preview: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class OAuthAccountORM(SQLAlchemyBaseOAuthAccountTableUUID, Base):
    """Linked OAuth identity (GitHub, Google, …).

    Inherits id (UUID), oauth_name, access_token, expires_at,
    refresh_token, account_id, account_email columns from
    SQLAlchemyBaseOAuthAccountTableUUID.

    ``user_id`` is overridden because the parent helper hardcodes
    ``ForeignKey("user.id", …)`` (singular table name) but our user
    table is ``users`` (plural — see UserORM.__tablename__). Without
    the override, SQLAlchemy raises NoReferencedTableError on import.

    Indexes are declared at the ORM level so ``Base.metadata.create_all``
    creates them on fresh DBs (it runs *before* ``apply_migrations`` —
    see ``persistence/db.py``). The migration provides idempotent
    fallbacks for existing DBs that predate this release.
    """

    __tablename__ = "oauth_accounts"
    __table_args__ = (
        # Every callback resolves an existing OAuth identity by
        # ``(oauth_name, account_id)``; without this composite the lookup
        # is a full table scan on every login (and login frequency
        # scales with active-user count).
        Index("ix_oauth_accounts_provider_account", "oauth_name", "account_id"),
    )

    @declared_attr
    def user_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            GUID,
            ForeignKey("users.id", ondelete="cascade"),
            nullable=False,
            # User-scoped queries (list / unlink an OAuth account from
            # the admin panel in Phase C) hit this column on every page;
            # an explicit single-column index keeps those fast even when
            # the composite above doesn't help (it leads with oauth_name).
            index=True,
        )


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class AgentORM(Base):
    """Logical agent — stable identity + pointer to current version."""

    __tablename__ = "agents"
    __table_args__ = (
        # Listing endpoints filter by user_id ("my agents") on every page;
        # keep the index aligned with the dominant access pattern.
        Index("ix_agents_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # Owner of the agent. CASCADE on user delete is intentional — a user
    # row that's been hard-deleted (admin / GDPR path, Phase C/E) takes
    # their owned data with it. Soft-deleted users keep their rows.
    # Owner of this row — set on every new resource by the route
    # handlers (sub-A4b). Nullable on the SQL side so the migration
    # can ADD COLUMN against pre-v0.3 dev DBs without DEFAULT
    # acrobatics; the backfill (#13) populates existing rows from a
    # synthetic ``system`` user, and Phase A's enforcement PR (sub-A4b)
    # will switch the application-layer contract to "always set on
    # create" — at which point the DB column flips to NOT NULL on
    # PostgreSQL (SQLite ALTER COLUMN limitations leave it nullable
    # there, but the ORM contract still holds).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="cascade"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    versions: Mapped[list[AgentVersionORM]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
        order_by="AgentVersionORM.version",
    )


class AgentVersionORM(Base):
    """Immutable agent version — full configuration snapshot.

    Includes display name so version history is fully recoverable even when
    the agent is renamed.
    """

    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    agent_id: Mapped[str] = mapped_column(String, ForeignKey("agents.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    name: Mapped[str] = mapped_column(String, nullable=False)
    runtime_id: Mapped[str] = mapped_column(String, nullable=False)
    runtime_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    # JSON column stores ``list[str]`` (PipelineState field names) as of
    # v0.5; older rows may still hold a dict — the Pydantic Agent model
    # coerces those at read-time. See packages/types/.../agent.py.
    input_schema: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    output_schema: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    constraints: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    budget_limit_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=60_000)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    agent: Mapped[AgentORM] = relationship(back_populates="versions")


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------


class PipelineORM(Base):
    """Logical pipeline — stable identity + pointer to current version."""

    __tablename__ = "pipelines"
    __table_args__ = (Index("ix_pipelines_user_id", "user_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # Owner of this row — set on every new resource by the route
    # handlers (sub-A4b). Nullable on the SQL side so the migration
    # can ADD COLUMN against pre-v0.3 dev DBs without DEFAULT
    # acrobatics; the backfill (#13) populates existing rows from a
    # synthetic ``system`` user, and Phase A's enforcement PR (sub-A4b)
    # will switch the application-layer contract to "always set on
    # create" — at which point the DB column flips to NOT NULL on
    # PostgreSQL (SQLite ALTER COLUMN limitations leave it nullable
    # there, but the ORM contract still holds).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="cascade"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    versions: Mapped[list[PipelineVersionORM]] = relationship(
        back_populates="pipeline",
        cascade="all, delete-orphan",
        order_by="PipelineVersionORM.version",
    )


class PipelineVersionORM(Base):
    """Immutable pipeline version — DAG snapshot.

    Includes name and description so version history reflects the metadata
    at the time of each save.
    """

    __tablename__ = "pipeline_versions"
    __table_args__ = (
        UniqueConstraint(
            "pipeline_id",
            "version",
            name="uq_pipeline_versions_pipeline_version",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    pipeline_id: Mapped[str] = mapped_column(String, ForeignKey("pipelines.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    schema_version: Mapped[str] = mapped_column(String, nullable=False, default="langgraph/1.0")
    state_schema_ref: Mapped[str] = mapped_column(String, nullable=False)
    entry_point: Mapped[str] = mapped_column(String, nullable=False)
    nodes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    edges: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    defaults: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    ui_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    pipeline: Mapped[PipelineORM] = relationship(back_populates="versions")


# ---------------------------------------------------------------------------
# Projects (v0.6) — workspace + workflow bindings
# ---------------------------------------------------------------------------


class ProjectORM(Base):
    """A project: working directory + binding of workflow kinds to pipelines.

    Single unversioned table — each project has one current-state row that
    is updated in place (only agents and pipelines are versioned). Bindings
    live in the JSON ``pipelines`` column; layered env vars in ``env_vars``.
    Both validated at write time by ``repo.create_project`` / ``update_project``.
    """

    __tablename__ = "projects"
    __table_args__ = (Index("ix_projects_user_id", "user_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # Owner of this row — set on every new resource by the route
    # handlers (sub-A4b). Nullable on the SQL side so the migration
    # can ADD COLUMN against pre-v0.3 dev DBs without DEFAULT
    # acrobatics; the backfill (#13) populates existing rows from a
    # synthetic ``system`` user, and Phase A's enforcement PR (sub-A4b)
    # will switch the application-layer contract to "always set on
    # create" — at which point the DB column flips to NOT NULL on
    # PostgreSQL (SQLite ALTER COLUMN limitations leave it nullable
    # there, but the ORM contract still holds).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="cascade"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    working_directory: Mapped[str | None] = mapped_column(String, nullable=True)
    repo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    default_branch: Mapped[str] = mapped_column(String, nullable=False, default="main")

    pipelines: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    env_vars: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Runs (no schema change in F2)
# ---------------------------------------------------------------------------


class RunORM(Base):
    """Pipeline execution instance — immutable FK to pipeline_versions row."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # Owner of this row — set on every new resource by the route
    # handlers (sub-A4b). Nullable on the SQL side so the migration
    # can ADD COLUMN against pre-v0.3 dev DBs without DEFAULT
    # acrobatics; the backfill (#13) populates existing rows from a
    # synthetic ``system`` user, and Phase A's enforcement PR (sub-A4b)
    # will switch the application-layer contract to "always set on
    # create" — at which point the DB column flips to NOT NULL on
    # PostgreSQL (SQLite ALTER COLUMN limitations leave it nullable
    # there, but the ORM contract still holds).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="cascade"),
        nullable=True,
    )
    # Owning project (v0.6, #64). FK with NO cascade — archiving a
    # project leaves its runs intact for historical inspection.
    # Nullable for ad-hoc / legacy runs triggered without a project.
    project_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("projects.id"), nullable=True, default=None
    )
    pipeline_id: Mapped[str] = mapped_column(String, nullable=False)
    pipeline_version: Mapped[int] = mapped_column(Integer, nullable=False)

    trigger_source: Mapped[str] = mapped_column(String, nullable=False)
    initial_state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    current_node: Mapped[str | None] = mapped_column(String, nullable=True)
    paused_at_node: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    gate_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, default=None)
    node_statuses: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)

    final_status: Mapped[str] = mapped_column(String, nullable=False)
    # Operator-facing reason for a non-success terminal status. Populated by
    # ``mark_stale_running_runs_as_failed`` on engine restart (#260) so the
    # dashboard / CLI can show why a run was forcibly terminated; ``None``
    # for runs that finished cleanly or were aborted/paused via a normal
    # operator action.
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    state_snapshots: Mapped[list[StateSnapshotORM]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="StateSnapshotORM.timestamp",
    )
    node_logs: Mapped[list[NodeExecutionLogORM]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="NodeExecutionLogORM.started_at",
    )

    __table_args__ = (
        # ``list_runs`` unfiltered: ORDER BY started_at DESC (#251).
        Index("ix_runs_started_at", "started_at"),
        # Project-scoped runs view: ``WHERE project_id = ? ORDER BY started_at
        # DESC``. Composite folds filter + sort into one index walk on SQLite,
        # which can't bitmap-intersect single-column indexes (#251).
        Index("ix_runs_project_started", "project_id", "started_at"),
        # Per-pipeline runs view: same shape with ``pipeline_id`` (#251).
        Index("ix_runs_pipeline_started", "pipeline_id", "started_at"),
        # Owner-scoped listings (admin cross-user view + dashboard's
        # "my runs" tab in Phase B). The composites above lead with
        # project_id / pipeline_id, so they don't help the user-only
        # filter; this single-column index keeps the access pattern
        # symmetric with the other resource tables (sub-A4a, #299).
        Index("ix_runs_user_id", "user_id"),
    )


class StateSnapshotORM(Base):
    __tablename__ = "state_snapshots"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    run: Mapped[RunORM] = relationship(back_populates="state_snapshots")


class NodeExecutionLogORM(Base):
    __tablename__ = "node_execution_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    node_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    runtime_id: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    prompt_xml: Mapped[str] = mapped_column(Text, nullable=False)
    stdout: Mapped[str] = mapped_column(Text, nullable=False, default="")
    stderr: Mapped[str] = mapped_column(Text, nullable=False, default="")
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    run: Mapped[RunORM] = relationship(back_populates="node_logs")

    __table_args__ = (
        # ``get_run`` populates node_statuses by ``WHERE run_id = ? ORDER BY
        # started_at`` on every detail fetch — high-frequency polling (#251).
        Index("ix_node_execution_logs_run_started", "run_id", "started_at"),
    )
