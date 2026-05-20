"""Settings and instance-level configuration ORM models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from dap_engine.persistence.model_base import Base


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
