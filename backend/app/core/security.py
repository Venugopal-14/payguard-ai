"""Opt-in API-key guard for state-changing routes.

Design (opt-in enforcement):
  * The key is read from server-side config only (``settings.payguard_api_key``,
    sourced from the ``PAYGUARD_API_KEY`` environment variable). It is NEVER a
    ``NEXT_PUBLIC_*`` value and is never logged.
  * If no key is configured, enforcement is OFF and the routes behave exactly as
    before. This preserves the current demo. SECURITY NOTE: while the key is
    unset, the state-changing routes remain unauthenticated and publicly
    reachable. Real protection requires BOTH configuring the key AND having the
    frontend authenticate through a server-side relay (not yet built).
  * If a key IS configured, the guarded routes require a matching
    ``X-API-Key`` header. Missing or wrong keys are rejected with 401, compared
    in constant time to avoid timing side-channels.

The guard is applied only to mission creation, approval, execution, and
capture. Read-only routes (health, metrics, listing) and the PayPal webhook
(which has its own signature verification) are intentionally NOT guarded.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from app.core.config import settings

API_KEY_HEADER = "X-API-Key"


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency enforcing the opt-in API key.

    * Enforcement disabled (no server key) -> allow (no-op).
    * Enforcement enabled -> require a constant-time-matching X-API-Key header.
    """
    configured = settings.payguard_api_key
    if not configured:
        # Opt-in: enforcement is off, preserve existing behavior.
        return

    # Compare as UTF-8 bytes so the constant-time guarantee of compare_digest
    # holds regardless of whether the configured key is ASCII or not.
    supplied = (x_api_key or "").encode("utf-8")
    expected = configured.encode("utf-8")
    if not x_api_key or not hmac.compare_digest(supplied, expected):
        # Do not reveal whether the header was missing vs wrong; do not log the
        # supplied value. A single generic rejection either way.
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")
