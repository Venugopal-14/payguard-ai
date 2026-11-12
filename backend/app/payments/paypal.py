"""PayPal Sandbox integration (Orders v2 + OAuth2).

Only this module talks to PayPal. The AI agent never does. Credentials come
from environment variables and are never logged.
"""

from __future__ import annotations

import base64
import logging
import uuid
from urllib.parse import urlparse

import httpx

from app.core.config import settings

_wh_log = logging.getLogger("payguard.webhooks")

# PayGuard is Sandbox-only. Every PayPal call goes through get_access_token(),
# which refuses any other API base URL, so live credentials/endpoints can never
# be used even if misconfigured.
SANDBOX_API_BASE = "https://api-m.sandbox.paypal.com"


class PayPalNotConfigured(RuntimeError):
    pass


class PayPalNotSandbox(RuntimeError):
    """Raised when PAYPAL_BASE_URL is not the PayPal Sandbox API."""


class PayPalAPIError(RuntimeError):
    """A non-2xx PayPal response, reduced to safe, non-secret fields.

    `issue` is PayPal's machine-readable reason (e.g. ORDER_NOT_APPROVED,
    INSTRUMENT_DECLINED, ORDER_ALREADY_CAPTURED). Never contains credentials.
    """

    def __init__(self, status_code: int, issue: str | None, debug_id: str | None):
        self.status_code = status_code
        self.issue = issue
        self.debug_id = debug_id
        super().__init__(f"PayPal HTTP {status_code} issue={issue} debug_id={debug_id}")

    @property
    def retryable(self) -> bool:
        """5xx/429 => outcome unknown or transient; safe to retry later."""
        return self.status_code >= 500 or self.status_code == 429


def _raise_for(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    issue = debug_id = None
    try:
        body = resp.json()
        debug_id = body.get("debug_id")
        details = body.get("details") or []
        issue = (details[0].get("issue") if details else None) or body.get("name")
    except Exception:  # noqa: BLE001 - non-JSON error body
        pass
    raise PayPalAPIError(resp.status_code, issue, debug_id)


def require_sandbox() -> None:
    if settings.paypal_base_url.rstrip("/") != SANDBOX_API_BASE:
        raise PayPalNotSandbox(
            "PAYPAL_BASE_URL must be the PayPal Sandbox API; live PayPal is not supported."
        )


def is_sandbox_approve_url(url: str | None) -> bool:
    """True only for an https PayPal *Sandbox* checkout URL."""
    if not url:
        return False
    try:
        p = urlparse(url)
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    return p.scheme == "https" and (
        host == "sandbox.paypal.com" or host.endswith(".sandbox.paypal.com")
    )


async def get_access_token() -> str:
    require_sandbox()
    if not settings.paypal_configured:
        raise PayPalNotConfigured(
            "PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set in .env"
        )

    credentials = f"{settings.paypal_client_id}:{settings.paypal_client_secret}"
    encoded = base64.b64encode(credentials.encode()).decode()

    headers = {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"grant_type": "client_credentials"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.paypal_base_url}/v1/oauth2/token",
            headers=headers,
            data=data,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def create_order(
    *, amount: float, currency: str = "USD", description: str = "",
    request_id: str | None = None,
) -> dict:
    token = await get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        # Idempotency key: PayPal dedupes retried create-order requests that
        # carry the same PayPal-Request-Id, preventing duplicate orders. Callers
        # pass a stable per-transaction id so retries/double-clicks reuse it.
        "PayPal-Request-Id": request_id or str(uuid.uuid4()),
    }

    unit: dict = {
        "amount": {"currency_code": currency, "value": f"{amount:.2f}"},
    }
    if description:
        unit["description"] = description[:127]

    payload = {
        "intent": "CAPTURE",
        "purchase_units": [unit],
        # Required for a redirect-based (non-JS-SDK) checkout so PayPal knows
        # where to send the buyer back after approval / cancellation. Omitting
        # return_url is a known cause of the generic sandbox checkout error.
        "application_context": {
            "brand_name": "PayGuard AI",
            "user_action": "PAY_NOW",
            "return_url": f"{settings.frontend_url}/paypal/return",
            "cancel_url": f"{settings.frontend_url}/paypal/cancel",
        },
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.paypal_base_url}/v2/checkout/orders",
            headers=headers,
            json=payload,
            timeout=30,
        )
        _raise_for(resp)
        return resp.json()


def _safe_order_id(order_id: str) -> str:
    # PayPal order ids are alphanumeric; refuse anything that could alter the URL.
    if not order_id or not order_id.isalnum() or len(order_id) > 64:
        raise ValueError("Invalid PayPal order id.")
    return order_id


async def capture_order(order_id: str, request_id: str | None = None) -> dict:
    token = await get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        # Same id on retry => PayPal returns the original capture result
        # instead of attempting a second capture.
        "PayPal-Request-Id": request_id or str(uuid.uuid4()),
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.paypal_base_url}/v2/checkout/orders/"
            f"{_safe_order_id(order_id)}/capture",
            headers=headers,
            json={},
            timeout=30,
        )
        _raise_for(resp)
        return resp.json()


async def get_order(order_id: str) -> dict:
    """Read-only order lookup (used to confirm state after an ambiguous result)."""
    token = await get_access_token()
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.paypal_base_url}/v2/checkout/orders/{_safe_order_id(order_id)}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        _raise_for(resp)
        return resp.json()


def extract_approve_url(order: dict) -> str | None:
    for link in order.get("links", []):
        if link.get("rel") == "approve":
            return link.get("href")
    return None


def extract_capture_id(capture_response: dict) -> str | None:
    try:
        return capture_response["purchase_units"][0]["payments"]["captures"][0]["id"]
    except (KeyError, IndexError, TypeError):
        return None


def extract_capture(order_or_capture_response: dict) -> dict | None:
    """First capture object (id, status, amount) from an order/capture response."""
    try:
        return order_or_capture_response["purchase_units"][0]["payments"]["captures"][0]
    except (KeyError, IndexError, TypeError):
        return None


# Headers PayPal sends with every webhook, needed for verification.
WEBHOOK_SIG_HEADERS = (
    "paypal-transmission-id",
    "paypal-transmission-time",
    "paypal-transmission-sig",
    "paypal-cert-url",
    "paypal-auth-algo",
)


async def verify_webhook_signature(*, headers: dict, raw_body: bytes) -> bool:
    """Verify a PayPal webhook using PayPal's verification API.

    Returns True only if PayPal confirms SUCCESS. Never trust a webhook payload
    that fails this check. Credentials/signatures are never logged.
    """
    if not settings.webhook_configured:
        raise PayPalNotConfigured("PAYPAL_WEBHOOK_ID is not set in .env")

    # Normalize header lookup (case-insensitive).
    lower = {k.lower(): v for k, v in headers.items()}
    missing = [h for h in WEBHOOK_SIG_HEADERS if h not in lower]
    if missing:
        # Log header NAMES only (never values) so we can tell a missing-header
        # case apart from a genuine signature FAILURE from PayPal.
        _wh_log.warning("verify_missing_headers headers=%s", ",".join(missing))
        return False

    import json as _json

    try:
        event = _json.loads(raw_body.decode("utf-8"))
    except Exception:
        _wh_log.warning("verify_bad_json_body")
        return False

    token = await get_access_token()
    payload = {
        "transmission_id": lower["paypal-transmission-id"],
        "transmission_time": lower["paypal-transmission-time"],
        "cert_url": lower["paypal-cert-url"],
        "auth_algo": lower["paypal-auth-algo"],
        "transmission_sig": lower["paypal-transmission-sig"],
        "webhook_id": settings.paypal_webhook_id,
        "webhook_event": event,
    }
    req_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.paypal_base_url}/v1/notifications/verify-webhook-signature",
            headers=req_headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        status = resp.json().get("verification_status")
        if status != "SUCCESS":
            # PayPal reachable but says the signature is not valid. The Webhook
            # Simulator always lands here because its events are not signed
            # against a real webhook; genuine delivered events verify SUCCESS.
            _wh_log.warning("verify_status_not_success status=%s", status)
        return status == "SUCCESS"
