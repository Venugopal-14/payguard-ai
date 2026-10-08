"""PayPal Sandbox integration (Orders v2 + OAuth2).

Only this module talks to PayPal. The AI agent never does. Credentials come
from environment variables and are never logged.
"""

from __future__ import annotations

import base64
import uuid

import httpx

from app.core.config import settings


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
