"""Resolve the decrypted instance env vars (#689 / #691).

Shared by the assistant chat and the AI error-explainer — both need to find a
provider key that may live encrypted in ``instance_env_vars`` (not os.environ).
Kept tiny and DB-only; callers merge with ``os.environ`` as needed.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from dap_engine.auth.encryption import EncryptionError, decrypt_value
from dap_engine.persistence.settings_models import InstanceEnvVarORM

logger = logging.getLogger("dap.engine.instance_env")


def load_instance_env(session: Session, fernet_key: str | None) -> dict[str, str]:
    """Decrypt the instance env vars into a ``{name: value}`` dict.

    Empty when no Fernet key is configured or no rows exist. A row that can't be
    decrypted (rotated key) is skipped with a warning rather than failing the
    whole request. Selects columns (not the ORM entity) so no tracked instances
    land in the session.
    """
    if not fernet_key:
        return {}
    out: dict[str, str] = {}
    rows = session.execute(select(InstanceEnvVarORM.key, InstanceEnvVarORM.ciphertext)).all()
    for key, ciphertext in rows:
        try:
            out[key] = decrypt_value(ciphertext, key=fernet_key)
        except EncryptionError:
            logger.warning("instance_env: could not decrypt %r — skipping", key)
    return out
