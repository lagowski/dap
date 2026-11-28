"""Backend error categories shared by the HTTP-speaking adapters (#934).

The category set itself lives in ``dap_types`` (``BackendErrorCategory``) so the
engine can decide on it without importing an adapter. This module only maps the
transport-level facts adapters see — an HTTP status, an httpx exception — onto it.
"""

from __future__ import annotations

from typing import Any, Final

import httpx
from dap_types import ERROR_CATEGORY_KEY, BackendErrorCategory

_HTTP_TOO_MANY_REQUESTS: Final = 429
_HTTP_SERVER_ERROR: Final = 500


def category_for_status(status: int | None) -> BackendErrorCategory | None:
    """429 is a rate limit and any 5xx means the backend is unavailable; else ``None``."""
    if status is None:
        return None
    if status == _HTTP_TOO_MANY_REQUESTS:
        return "rate_limit"
    if status >= _HTTP_SERVER_ERROR:
        return "unavailable"
    return None


def category_for_transport_error(exc: httpx.RequestError) -> BackendErrorCategory | None:
    """A timeout, or a network / protocol failure talking to the backend.

    ``UnsupportedProtocol`` and the like stay uncategorised: they come from a bad
    URL in the node's config, which no other backend would fix.
    """
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, (httpx.NetworkError, httpx.RemoteProtocolError, httpx.ProxyError)):
        return "unavailable"
    return None


def with_category(
    structured: dict[str, Any], category: BackendErrorCategory | None
) -> dict[str, Any]:
    """Set the category on a failed result's ``structured`` dict; absent when ``None``."""
    if category is not None:
        structured[ERROR_CATEGORY_KEY] = category
    return structured


__all__ = ["category_for_status", "category_for_transport_error", "with_category"]
