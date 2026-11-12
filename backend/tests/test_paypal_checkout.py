"""PayPal Sandbox checkout redirect flow — fully mocked, no network, no payments.

Flow under test:
  mission -> AWAITING_APPROVAL -> human approve -> APPROVED
  -> execute (Sandbox order + approve_url) -> PAYPAL_CREATED
  -> buyer returns (token) -> capture -> verify PayPal response -> COMPLETED

PayPal is replaced by an in-memory fake (FakePayPal) at the module boundary for
API tests, and by httpx.MockTransport for the low-level paypal.py tests. Every
mocked response mirrors the real Orders v2 shapes; nothing reaches PayPal.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.models.audit_event import AuditEvent
from app.models.transaction import Transaction
from app.payments import paypal

ORDER = "5O190127TN364715T"
APPROVE_URL = f"https://www.sandbox.paypal.com/checkoutnow?token={ORDER}"


class FakePayPal:
    """In-memory stand-in for the PayPal Orders v2 API (Sandbox semantics)."""

    def __init__(self):
        self.orders_by_request_id: dict[str, dict] = {}
        self.create_calls = 0
        self.capture_calls: list[str | None] = []
        self.capture_plan: list = []   # queued responses/exceptions for capture
        self.get_order_resp: dict | None = None
        self.approve_href = APPROVE_URL
        self.amount = None
        self.currency = None

    async def create_order(self, *, amount, currency="USD", description="", request_id=None):
        await asyncio.sleep(0)  # yield, so concurrent callers really interleave
        self.create_calls += 1
        # PayPal-Request-Id idempotency: same id -> same order.
        if request_id in self.orders_by_request_id:
            return self.orders_by_request_id[request_id]
        self.amount, self.currency = f"{amount:.2f}", currency
        order = {"id": ORDER, "status": "CREATED", "links": [
            {"rel": "self", "href": f"https://api-m.sandbox.paypal.com/v2/checkout/orders/{ORDER}"},
            {"rel": "approve", "href": self.approve_href},
        ]}
        self.orders_by_request_id[request_id] = order
        return order

    def completed(self, status="COMPLETED", cap_status="COMPLETED", value=None, currency=None,
                  order_id=ORDER):
        return {"id": order_id, "status": status, "purchase_units": [{"payments": {"captures": [{
            "id": "3C679366HH908993F", "status": cap_status,
            "amount": {"currency_code": currency or self.currency,
                       "value": value or self.amount}}]}}]}

    async def capture_order(self, order_id, request_id=None):
        self.capture_calls.append(request_id)
        item = self.capture_plan.pop(0) if self.capture_plan else self.completed()
        if isinstance(item, Exception):
            raise item
        return item

    async def get_order(self, order_id):
        return self.get_order_resp or self.completed()


@pytest.fixture()
def env(monkeypatch):
    from app.api.payguard import router
    from app.db.database import Base, get_db

    monkeypatch.setattr(settings, "payguard_api_key", "", raising=False)
    monkeypatch.setattr(settings, "ollama_base_url", "", raising=False)
    monkeypatch.setattr(settings, "ollama_model", "", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)

    from app.api import payguard as api
    monkeypatch.setattr(api, "_txn_locks", {})  # fresh per-test locks

    fake = FakePayPal()
    monkeypatch.setattr(paypal, "create_order", fake.create_order)
    monkeypatch.setattr(paypal, "capture_order", fake.capture_order)
    monkeypatch.setattr(paypal, "get_order", fake.get_order)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = _db
    return TestClient(app), Session, fake


def _approved_txn(client):
    r = client.post("/api/missions", json={
        "instruction": "Buy a keyboard under $50 from Amazon", "budget": 50,
        "currency": "USD", "approval_threshold": 200})
    t = r.json()["transaction"]
    assert t["status"] == "AWAITING_APPROVAL"
    assert client.post(f"/api/transactions/{t['id']}/approve").status_code == 200
    return t["id"]


def _checkout(client, tid):
    r = client.post("/api/transactions/execute", json={"transaction_id": tid})
    assert r.status_code == 200, r.text
    return r.json()


def _return(client, token=ORDER, payer="QYR5Z8XDVJNXQ"):
    return client.post("/api/paypal/return", json={"token": token, "payer_id": payer})


def _events(Session, tid):
    s = Session()
    try:
        return [e.event_type for e in s.query(AuditEvent)
                .filter(AuditEvent.transaction_id == tid).order_by(AuditEvent.id).all()]
    finally:
        s.close()


def _txn(Session, tid):
    s = Session()
    try:
        t = s.get(Transaction, tid)
        s.expunge(t)
        return t
    finally:
        s.close()


# ----------------------------------------------------------- happy path

def test_full_flow_redirect_return_capture_completed(env):
    client, Session, fake = env
    tid = _approved_txn(client)

    t = _checkout(client, tid)
    assert t["status"] == "PAYPAL_CREATED"
    assert t["paypal_order_id"] == ORDER
    assert t["approve_url"] == APPROVE_URL          # real PayPal-hosted URL
    assert paypal.is_sandbox_approve_url(t["approve_url"])
    assert t["paypal_capture_id"] is None           # nothing captured yet

    r = _return(client)
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "COMPLETED"
    assert body["transaction"]["status"] == "COMPLETED"
    assert body["transaction"]["paypal_capture_id"] == "3C679366HH908993F"

    ev = _events(Session, tid)
    for expected in ("HUMAN_APPROVED", "ORDER_CREATED", "PAYPAL_BUYER_RETURNED",
                     "CAPTURE_VERIFIED"):
        assert expected in ev
    # Human approval happens before PayPal checkout approval.
    assert ev.index("HUMAN_APPROVED") < ev.index("ORDER_CREATED") < ev.index("PAYPAL_BUYER_RETURNED")


def test_stable_request_ids_are_used(env):
    client, Session, fake = env
    tid = _approved_txn(client)
    _checkout(client, tid)
    _return(client)
    rid = list(fake.orders_by_request_id)[0]
    assert rid.startswith("payguard-order-")
    assert fake.capture_calls[0].startswith("payguard-capture-")


# ----------------------------------------------------------- gating

def test_execute_requires_human_approval(env):
    client, _, fake = env
    r = client.post("/api/missions", json={
        "instruction": "Buy a keyboard under $50 from Amazon", "budget": 50,
        "currency": "USD", "approval_threshold": 200})
    tid = r.json()["transaction"]["id"]
    r = client.post("/api/transactions/execute", json={"transaction_id": tid})
    assert r.status_code == 403
    assert fake.create_calls == 0


def test_non_sandbox_approve_url_rejected(env):
    client, Session, fake = env
    fake.approve_href = f"https://www.paypal.com/checkoutnow?token={ORDER}"  # LIVE host
    tid = _approved_txn(client)
    r = client.post("/api/transactions/execute", json={"transaction_id": tid})
    assert r.status_code == 502
    t = _txn(Session, tid)
    assert t.approve_url is None and t.status == "FAILED"


# ----------------------------------------------------------- duplicates

def test_duplicate_execute_returns_same_order(env):
    client, Session, fake = env
    tid = _approved_txn(client)
    a = _checkout(client, tid)
    b = _checkout(client, tid)
    assert a["paypal_order_id"] == b["paypal_order_id"] == ORDER
    assert b["approve_url"] == APPROVE_URL
    assert fake.create_calls == 1
    assert "EXECUTE_DUPLICATE" in _events(Session, tid)


def test_concurrent_execute_creates_one_order(env):
    """Two simultaneous executes in one process -> one PayPal order."""
    client, Session, fake = env
    tid = _approved_txn(client)

    from app.api import payguard as api
    from app.schemas.payment import ExecuteRequest

    async def both():
        s1, s2 = Session(), Session()
        try:
            return await asyncio.gather(
                api.execute_transaction(ExecuteRequest(transaction_id=tid), s1),
                api.execute_transaction(ExecuteRequest(transaction_id=tid), s2),
            )
        finally:
            s1.close()
            s2.close()

    r1, r2 = asyncio.run(both())
    assert r1.paypal_order_id == r2.paypal_order_id == ORDER
    assert fake.create_calls == 1


def test_return_is_idempotent(env):
    client, Session, fake = env
    tid = _approved_txn(client)
    _checkout(client, tid)
    assert _return(client).json()["outcome"] == "COMPLETED"
    again = _return(client).json()
    assert again["outcome"] == "ALREADY_COMPLETED"
    assert again["transaction"]["status"] == "COMPLETED"
    assert len(fake.capture_calls) == 1               # never captured twice


def test_manual_capture_after_return_is_safe(env):
    client, _, fake = env
    tid = _approved_txn(client)
    _checkout(client, tid)
    _return(client)
    r = client.post("/api/transactions/capture", json={"transaction_id": tid})
    assert r.status_code == 200 and r.json()["status"] == "COMPLETED"
    assert len(fake.capture_calls) == 1


def test_already_captured_is_confirmed_via_get_order(env):
    client, _, fake = env
    tid = _approved_txn(client)
    _checkout(client, tid)
    fake.capture_plan = [paypal.PayPalAPIError(422, "ORDER_ALREADY_CAPTURED", "dbg")]
    body = _return(client).json()
    assert body["outcome"] == "COMPLETED"
    assert body["transaction"]["status"] == "COMPLETED"


# ----------------------------------------------------------- cancel / not approved

def test_cancel_keeps_paypal_created_and_allows_retry(env):
    client, Session, fake = env
    tid = _approved_txn(client)
    _checkout(client, tid)
    r = client.post("/api/paypal/cancel", json={"token": ORDER})
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "CANCELLED"
    assert body["transaction"]["status"] == "PAYPAL_CREATED"
    assert body["retry_url"] == APPROVE_URL
    assert "PAYPAL_CHECKOUT_CANCELLED" in _events(Session, tid)
    assert fake.capture_calls == []                   # cancel never captures
    # Retry: buyer goes back, approves, returns -> completes.
    assert _return(client).json()["outcome"] == "COMPLETED"


def test_capture_before_buyer_approval_keeps_state(env):
    client, Session, fake = env
    tid = _approved_txn(client)
    _checkout(client, tid)
    fake.capture_plan = [paypal.PayPalAPIError(422, "ORDER_NOT_APPROVED", "dbg")]
    r = client.post("/api/transactions/capture", json={"transaction_id": tid})
    assert r.status_code == 409
    assert _txn(Session, tid).status == "PAYPAL_CREATED"   # NOT failed
    assert "CAPTURE_NOT_APPROVED" in _events(Session, tid)


def test_instrument_declined_offers_retry(env):
    client, Session, fake = env
    tid = _approved_txn(client)
    _checkout(client, tid)
    fake.capture_plan = [paypal.PayPalAPIError(422, "INSTRUMENT_DECLINED", "dbg")]
    body = _return(client).json()
    assert body["outcome"] == "DECLINED_RETRY"
    assert body["retry_url"] == APPROVE_URL
    assert body["transaction"]["status"] == "PAYPAL_CREATED"


# ----------------------------------------------------------- failures

def test_pending_capture_stays_pending_then_webhook_completes(env):
    client, Session, fake = env
    tid = _approved_txn(client)
    _checkout(client, tid)
    fake.capture_plan = [fake.completed(status="COMPLETED", cap_status="PENDING")]
    body = _return(client).json()
    assert body["outcome"] == "PENDING"
    assert body["transaction"]["status"] == "PAYPAL_CREATED"
    assert body["transaction"]["paypal_capture_id"] == "3C679366HH908993F"

    from app.payments.webhooks import process_event
    s = Session()
    try:
        res = process_event(s, event_id="WH-1", event_type="PAYMENT.CAPTURE.COMPLETED",
                            resource={"id": "3C679366HH908993F",
                                      "amount": {"currency_code": fake.currency,
                                                 "value": fake.amount},
                                      "supplementary_data": {"related_ids": {"order_id": ORDER}}})
    finally:
        s.close()
    assert "COMPLETED" in res
    assert _txn(Session, tid).status == "COMPLETED"


def test_declined_capture_marks_failed(env):
    client, Session, fake = env
    tid = _approved_txn(client)
    _checkout(client, tid)
    fake.capture_plan = [fake.completed(status="COMPLETED", cap_status="DECLINED")]
    body = _return(client).json()
    assert body["outcome"] == "FAILED"
    assert _txn(Session, tid).status == "FAILED"
    assert "CAPTURE_DECLINED" in _events(Session, tid)


def test_amount_mismatch_never_completes(env):
    client, Session, fake = env
    tid = _approved_txn(client)
    _checkout(client, tid)
    fake.capture_plan = [fake.completed(value="999.99")]
    body = _return(client).json()
    assert body["outcome"] == "FAILED"
    assert _txn(Session, tid).status == "FAILED"
    assert "CAPTURE_VERIFICATION_FAILED" in _events(Session, tid)


def test_order_id_mismatch_never_completes(env):
    client, Session, fake = env
    tid = _approved_txn(client)
    _checkout(client, tid)
    fake.capture_plan = [fake.completed(order_id="OTHERORDER1")]
    assert _return(client).json()["outcome"] == "FAILED"
    assert _txn(Session, tid).status != "COMPLETED"


def test_network_error_keeps_state_and_retry_succeeds(env):
    client, Session, fake = env
    tid = _approved_txn(client)
    _checkout(client, tid)
    fake.capture_plan = [httpx.ConnectError("boom")]
    body = _return(client).json()
    assert body["outcome"] == "RETRYABLE_ERROR"
    assert _txn(Session, tid).status == "PAYPAL_CREATED"
    # Same stable request id on retry; this time PayPal confirms.
    retry = _return(client).json()
    assert retry["outcome"] == "COMPLETED"
    assert fake.capture_calls[0] == fake.capture_calls[1]


def test_paypal_5xx_is_retryable_not_failed(env):
    client, Session, fake = env
    tid = _approved_txn(client)
    _checkout(client, tid)
    fake.capture_plan = [paypal.PayPalAPIError(503, None, "dbg")]
    r = client.post("/api/transactions/capture", json={"transaction_id": tid})
    assert r.status_code == 502
    assert "safe to retry" in r.json()["detail"]
    assert _txn(Session, tid).status == "PAYPAL_CREATED"


def test_paypal_4xx_rejection_marks_failed(env):
    client, Session, fake = env
    tid = _approved_txn(client)
    _checkout(client, tid)
    fake.capture_plan = [paypal.PayPalAPIError(404, "RESOURCE_NOT_FOUND", "dbg")]
    assert _return(client).json()["outcome"] == "FAILED"
    assert _txn(Session, tid).status == "FAILED"


def test_order_create_transient_error_keeps_approved(env, monkeypatch):
    client, Session, fake = env
    tid = _approved_txn(client)

    async def _timeout(**kw):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(paypal, "create_order", _timeout)
    r = client.post("/api/transactions/execute", json={"transaction_id": tid})
    assert r.status_code == 502 and "safe to retry" in r.json()["detail"]
    assert _txn(Session, tid).status == "APPROVED"


def test_unknown_token_is_404(env):
    client, _, _ = env
    assert _return(client, token="NOSUCHORDER1").status_code == 404
    assert client.post("/api/paypal/cancel", json={"token": "NOSUCHORDER1"}).status_code == 404


def test_malformed_token_rejected(env):
    client, _, _ = env
    assert _return(client, token="../../admin").status_code == 422


def test_return_on_blocked_or_unpaid_txn_does_not_capture(env):
    client, Session, fake = env
    tid = _approved_txn(client)
    _checkout(client, tid)
    s = Session()
    t = s.get(Transaction, tid)
    t.status = "FAILED"
    s.commit()
    s.close()
    body = _return(client).json()
    assert body["outcome"] == "NOT_CAPTURABLE"
    assert fake.capture_calls == []


# ----------------------------------------------------------- webhook safety

def test_webhook_amount_mismatch_not_reconciled(env):
    client, Session, fake = env
    tid = _approved_txn(client)
    _checkout(client, tid)
    from app.payments.webhooks import process_event
    s = Session()
    try:
        res = process_event(s, event_id="WH-BAD", event_type="PAYMENT.CAPTURE.COMPLETED",
                            resource={"id": "CAPX", "amount": {"currency_code": "USD",
                                                               "value": "1.00"},
                                      "supplementary_data": {"related_ids": {"order_id": ORDER}}})
    finally:
        s.close()
    assert "mismatch" in res
    assert _txn(Session, tid).status == "PAYPAL_CREATED"


# ----------------------------------------------------------- paypal.py (MockTransport)

def _mock_http(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*a, **k):
        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)

    monkeypatch.setattr(paypal.httpx, "AsyncClient", factory)


@pytest.fixture()
def creds(monkeypatch):
    monkeypatch.setattr(settings, "paypal_client_id", "test-client", raising=False)
    monkeypatch.setattr(settings, "paypal_client_secret", "test-secret", raising=False)
    monkeypatch.setattr(settings, "paypal_base_url", paypal.SANDBOX_API_BASE, raising=False)


def test_live_base_url_refused(monkeypatch, creds):
    monkeypatch.setattr(settings, "paypal_base_url", "https://api-m.paypal.com", raising=False)
    calls = []
    _mock_http(monkeypatch, lambda req: calls.append(req) or httpx.Response(200, json={}))
    with pytest.raises(paypal.PayPalNotSandbox):
        asyncio.run(paypal.create_order(amount=1.0))
    assert calls == []  # no request was even attempted


def test_http_requests_target_sandbox_with_request_ids(monkeypatch, creds):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok"})
        if req.url.path == "/v2/checkout/orders":
            body = json.loads(req.content)
            ctx = body["application_context"]
            assert ctx["return_url"].endswith("/paypal/return")
            assert ctx["cancel_url"].endswith("/paypal/cancel")
            return httpx.Response(201, json={"id": ORDER, "links": [
                {"rel": "approve", "href": APPROVE_URL}]})
        if req.url.path == f"/v2/checkout/orders/{ORDER}/capture":
            return httpx.Response(201, json={"id": ORDER, "status": "COMPLETED"})
        return httpx.Response(404)

    _mock_http(monkeypatch, handler)
    order = asyncio.run(paypal.create_order(amount=12.5, request_id="payguard-order-abc"))
    asyncio.run(paypal.capture_order(ORDER, request_id="payguard-capture-abc"))
    assert paypal.extract_approve_url(order) == APPROVE_URL
    assert all(r.url.host == "api-m.sandbox.paypal.com" for r in seen)
    ids = [r.headers.get("PayPal-Request-Id") for r in seen if "checkout" in r.url.path]
    assert ids == ["payguard-order-abc", "payguard-capture-abc"]
    assert all("test-secret" not in str(r.url) for r in seen)


def test_paypal_error_issue_is_parsed(monkeypatch, creds):
    def handler(req):
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(422, json={
            "name": "UNPROCESSABLE_ENTITY", "debug_id": "abc123",
            "details": [{"issue": "ORDER_NOT_APPROVED"}]})

    _mock_http(monkeypatch, handler)
    with pytest.raises(paypal.PayPalAPIError) as exc:
        asyncio.run(paypal.capture_order(ORDER))
    assert exc.value.status_code == 422
    assert exc.value.issue == "ORDER_NOT_APPROVED"
    assert exc.value.retryable is False


def test_capture_rejects_unsafe_order_id(monkeypatch, creds):
    _mock_http(monkeypatch, lambda req: httpx.Response(200, json={"access_token": "tok"}))
    with pytest.raises(ValueError):
        asyncio.run(paypal.capture_order("../../v1/oauth2/token"))


@pytest.mark.parametrize("url,ok", [
    (APPROVE_URL, True),
    ("https://sandbox.paypal.com/checkoutnow?token=X", True),
    ("https://www.paypal.com/checkoutnow?token=X", False),            # live
    ("http://www.sandbox.paypal.com/checkoutnow?token=X", False),     # not https
    ("https://www.sandbox.paypal.com.evil.example/x", False),          # lookalike
    ("javascript:alert(1)", False),
    (None, False),
])
def test_is_sandbox_approve_url(url, ok):
    assert paypal.is_sandbox_approve_url(url) is ok
