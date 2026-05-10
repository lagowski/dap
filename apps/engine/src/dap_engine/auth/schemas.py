"""Pydantic schemas exposed to fastapi-users.

The base classes (``schemas.BaseUser``, ``BaseUserCreate``,
``BaseUserUpdate``) bring the auth-mandatory fields (id, email,
password, is_active, is_superuser, is_verified). ``UserRead`` mixes in
``BaseOAuthAccountMixin`` so the OAuth callback response includes the
linked ``oauth_accounts`` list — fastapi-users serialises the
relationship into this field automatically when the User model has an
``oauth_accounts`` Mapped attribute.

We don't add custom fields here — the ``created_at`` / ``last_login_at``
columns on the ORM aren't user-settable through the API, so they don't
appear in the schemas.
"""

from __future__ import annotations

import uuid

from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID], schemas.BaseOAuthAccountMixin):
    """Public user representation returned by ``GET /users/me`` etc.

    The ``BaseOAuthAccountMixin`` mixin adds
    ``oauth_accounts: list[BaseOAuthAccount]`` so OAuth callbacks
    return linked-identity info alongside the standard user fields.
    """


class UserCreate(schemas.BaseUserCreate):
    """Payload for ``POST /auth/register``."""


class UserUpdate(schemas.BaseUserUpdate):
    """Payload for ``PATCH /users/me`` (and admin user updates)."""
