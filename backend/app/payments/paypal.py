"""PayPal Sandbox integration (Orders v2 + OAuth2).

Only this module talks to PayPal. The AI agent never does. Credentials come
from environment variables and are never logged.
"""

from __future__ import annotations

import base64
import logging
import uuid

import httpx

from app.core.config import settings

_wh_log = logging.getLogger("payguard.webhooks")


class PayPalNotConfigured(RuntimeError):
    pass


async def get_access_token() -> str:
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
    *, amount: float, currency: str = "USD", description: str = ""
) -> dict:
    token = await get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        # Idempotency key: PayPal dedupes retried create-order requests that
        # carry the same PayPal-Request-Id, preventing duplicate orders.
        "PayPal-Request-Id": str(uuid.uuid4()),
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
        resp.raise_for_status()
        return resp.json()


async def capture_order(order_id: str) -> dict:
    token = await get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.paypal_base_url}/v2/checkout/orders/{order_id}/capture",
            headers=headers,
            json={},
            timeout=30,
        )
        resp.raise_for_status()
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
