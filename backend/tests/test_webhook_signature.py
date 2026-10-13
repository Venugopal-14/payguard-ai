"""Phase 4A/4D tests for PayPal webhook signature verification.

PayPal network calls are mocked, so no real PayPal interaction occurs.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.core.config import settings
from app.payments import paypal

GOOD_HEADERS = {
    "paypal-transmission-id": "tid",
    "paypal-transmission-time": "2026-01-01T00:00:00Z",
    "paypal-transmission-sig": "sig",
    "paypal-cert-url": "https://api.sandbox.paypal.com/cert",
    "paypal-auth-algo": "SHA256withRSA",
}
BODY = b'{"id":"EVT-1","event_type":"PAYMENT.CAPTURE.COMPLETED","resource":{}}'


def _patch_verify(monkeypatch, status: str):
    async def fake_token():
        return "fake-token"

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"verification_status": status}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(paypal, "get_access_token", fake_token)
    monkeypatch.setattr(paypal.httpx, "AsyncClient", lambda *a, **k: FakeClient())
    monkeypatch.setattr(settings, "paypal_webhook_id", "WH-TEST", raising=False)


def test_valid_signature_accepted(monkeypatch):
    _patch_verify(monkeypatch, "SUCCESS")
    ok = asyncio.run(
        paypal.verify_webhook_signature(headers=dict(GOOD_HEADERS), raw_body=BODY)
    )
    assert ok is True


def test_invalid_signature_rejected(monkeypatch):
    _patch_verify(monkeypatch, "FAILURE")
    ok = asyncio.run(
        paypal.verify_webhook_signature(headers=dict(GOOD_HEADERS), raw_body=BODY)
    )
    assert ok is False


def test_missing_headers_rejected(monkeypatch):
    monkeypatch.setattr(settings, "paypal_webhook_id", "WH-TEST", raising=False)
    partial = dict(GOOD_HEADERS)
    del partial["paypal-transmission-sig"]
    ok = asyncio.run(
        paypal.verify_webhook_signature(headers=partial, raw_body=BODY)
    )
    assert ok is False


def test_verification_api_error_bubbles(monkeypatch):
    async def fake_token():
        return "fake-token"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise httpx.ConnectError("network down")

    monkeypatch.setattr(paypal, "get_access_token", fake_token)
    monkeypatch.setattr(paypal.httpx, "AsyncClient", lambda *a, **k: FakeClient())
    monkeypatch.setattr(settings, "paypal_webhook_id", "WH-TEST", raising=False)

    with pytest.raises(Exception):
        asyncio.run(
            paypal.verify_webhook_signature(headers=dict(GOOD_HEADERS), raw_body=BODY)
        )


def test_not_configured_raises(monkeypatch):
    monkeypatch.setattr(settings, "paypal_webhook_id", "", raising=False)
    with pytest.raises(paypal.PayPalNotConfigured):
        asyncio.run(
            paypal.verify_webhook_signature(headers=dict(GOOD_HEADERS), raw_body=BODY)
        )
