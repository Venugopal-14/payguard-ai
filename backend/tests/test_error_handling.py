"""Tests for Fix #2: PayPal failures must not leak internal detail to clients.

When PayPal order creation or capture raises, the API must:
  * return HTTP 502 with a GENERIC message,
  * never include the raw exception text (a sentinel) in the response,
  * still perform the FAILED lifecycle transition.

PayPal calls are monkeypatched to raise; no real PayPal/DB is used (in-memory
SQLite via StaticPool so the app thread shares the schema/data).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings

SENTINEL = "SENSITIVE-INTERNAL-DETAIL"


@pytest.fixture()
def ctx(monkeypatch):
    """TestClient + a shared Session factory, API-key guard disabled."""
    from app.db.database import Base, get_db
    from app.api.payguard import router as payguard_router

    # Keep the opt-in guard OFF so these tests exercise the error path, not auth.
    monkeypatch.setattr(settings, "payguard_api_key", "", raising=False)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def _override_get_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(payguard_router)
    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app), Session


def _seed_txn(Session, *, status, order_id=None):
    from app.models.transaction import Transaction

    s = Session()
    t = Transaction(
        description="USB cable", merchant="Amazon", amount=20, currency="USD",
        decision="APPROVE", risk_score=10, risk_level="LOW", reasoning="",
        status=status, paypal_order_id=order_id, correlation_id="corr-err",
    )
    s.add(t)
    s.commit()
    s.refresh(t)
    tid = t.id
    s.close()
    return tid


def test_execute_failure_returns_generic_message_no_leak(ctx, monkeypatch):
    client, Session = ctx
    tid = _seed_txn(Session, status="APPROVED")

    from app.payments import paypal

    async def _boom(*args, **kwargs):
        raise RuntimeError(f"{SENTINEL}: boom from PayPal order")

    monkeypatch.setattr(paypal, "create_order", _boom)

    r = client.post("/api/transactions/execute", json={"transaction_id": tid})
    assert r.status_code == 502
    body = r.text
    assert "PayPal order creation failed." in r.json()["detail"]
    assert SENTINEL not in body  # internal detail must not leak

    # FAILED lifecycle transition still happened.
    from app.models.transaction import Transaction
    s = Session()
    t = s.get(Transaction, tid)
    assert t.status == "FAILED"
    s.close()


def test_capture_failure_returns_generic_message_no_leak(ctx, monkeypatch):
    client, Session = ctx
    tid = _seed_txn(Session, status="PAYPAL_CREATED", order_id="ORDER-XYZ")

    from app.payments import paypal

    async def _boom(*args, **kwargs):
        raise RuntimeError(f"{SENTINEL}: boom from PayPal capture")

    monkeypatch.setattr(paypal, "capture_order", _boom)

    r = client.post("/api/transactions/capture", json={"transaction_id": tid})
    assert r.status_code == 502
    body = r.text
    assert "PayPal capture failed." in r.json()["detail"]
    assert SENTINEL not in body

    from app.models.transaction import Transaction
    s = Session()
    t = s.get(Transaction, tid)
    assert t.status == "FAILED"
    s.close()


def test_execute_not_configured_still_503_self_authored(ctx, monkeypatch):
    """The self-authored PayPalNotConfigured 503 is intentionally unchanged."""
    client, Session = ctx
    tid = _seed_txn(Session, status="APPROVED")

    from app.payments import paypal

    async def _not_configured(*args, **kwargs):
        raise paypal.PayPalNotConfigured(
            "PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set in .env"
        )

    monkeypatch.setattr(paypal, "create_order", _not_configured)

    r = client.post("/api/transactions/execute", json={"transaction_id": tid})
    assert r.status_code == 503
