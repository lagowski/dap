"""Pydantic schemas exposed to fastapi-users.

The base classes (``schemas.BaseUser``, ``BaseUserCreate``,
``BaseUserUpdate``) bring the auth-mandatory fields (id, email,
password, is_active, is_superuser, is_verified). We don't add custom
fields in this PR — the ``created_at`` / ``last_login_at`` columns on
the ORM aren't user-settable through the API, so they don't appear in
the schemas.
"""

from __future__ import annotations

import uuid

from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID]):
    """Public user representation returned by ``GET /users/me`` etc."""


class UserCreate(schemas.BaseUserCreate):
    """Payload for ``POST /auth/register``."""


class UserUpdate(schemas.BaseUserUpdate):
    """Payload for ``PATCH /users/me`` (and admin user updates)."""
