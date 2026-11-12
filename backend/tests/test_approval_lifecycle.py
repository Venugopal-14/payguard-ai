"""Regression tests: policy recommendation vs. human approval vs. payment.

Invariants locked in here:
  * Every non-blocked mission starts AWAITING_APPROVAL ("Pending approval"),
    even when the policy recommendation is APPROVE.
  * `decision` (policy recommendation) is stored once and never changes.
  * APPROVED is only reachable via an explicit human approval.
  * No PayPal order before human approval; COMPLETED only after a PayPal
    capture that reports COMPLETED.
  * The orchestrator handoff (same budget + threshold) preserves the
    recommendation shown in the dry run.

PayPal is always monkeypatched; no network, no real payments. The agent uses
the deterministic fallback (no LLM). In-memory SQLite via StaticPool.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.engine import lifecycle
from app.models.audit_event import AuditEvent
from app.models.transaction import Transaction

KEYBOARD = "Buy a keyboard under $50 from Amazon"


@pytest.fixture()
def ctx(monkeypatch):
    from app.api.payguard import router as payguard_router
    from app.db.database import Base, get_db

    monkeypatch.setattr(settings, "payguard_api_key", "", raising=False)
    monkeypatch.setattr(settings, "ollama_base_url", "", raising=False)
    monkeypatch.setattr(settings, "ollama_model", "", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)

    # Fail loudly if anything tries to reach PayPal without a test patch.
    from app.payments import paypal

    async def _no_network(*a, **k):
        raise AssertionError("PayPal must not be called in this test")

    monkeypatch.setattr(paypal, "create_order", _no_network)
    monkeypatch.setattr(paypal, "capture_order", _no_network)

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(payguard_router)
    app.dependency_overrides[get_db] = _db
    return TestClient(app), Session


def _mission(client, *, instruction=KEYBOARD, budget=50, threshold=200):
    r = client.post("/api/missions", json={
        "instruction": instruction, "budget": budget,
        "currency": "USD", "approval_threshold": threshold,
    })
    assert r.status_code == 200, r.text
    return r.json()["transaction"]


def _events(Session, tid):
    s = Session()
    try:
        return [
            (e.actor, e.event_type, e.previous_state, e.new_state)
            for e in s.query(AuditEvent)
            .filter(AuditEvent.transaction_id == tid)
            .order_by(AuditEvent.id.asc())
            .all()
        ]
    finally:
        s.close()


def _patch_paypal_ok(monkeypatch, capture_status="COMPLETED"):
    from app.payments import paypal

    state = {}

    async def _create(**kw):
        state["amount"] = f"{kw['amount']:.2f}"
        state["currency"] = kw["currency"]
        return {"id": "ORDERTEST1", "status": "CREATED", "links": [
            {"rel": "approve",
             "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORDERTEST1"}]}

    async def _capture(order_id, request_id=None):
        return {"id": order_id, "status": capture_status, "purchase_units": [{"payments": {
            "captures": [{"id": "CAP-TEST", "status": capture_status,
                          "amount": {"currency_code": state["currency"],
                                     "value": state["amount"]}}]}}]}

    monkeypatch.setattr(paypal, "create_order", _create)
    monkeypatch.setattr(paypal, "capture_order", _capture)


# ---------------------------------------------------------------- creation

def test_policy_approve_still_waits_for_human(ctx):
    client, _ = ctx
    t = _mission(client, threshold=200)  # below threshold, LOW risk
    assert t["decision"] == "APPROVE"            # recommendation preserved
    assert t["status"] == "AWAITING_APPROVAL"    # NOT auto-approved
    assert t["human_approval"] == "PENDING"


def test_policy_needs_approval_waits_for_human(ctx):
    client, _ = ctx
    t = _mission(client, threshold=20)  # amount >= threshold
    assert t["decision"] == "NEEDS_APPROVAL"
    assert t["status"] == "AWAITING_APPROVAL"
    assert t["human_approval"] == "PENDING"


def test_policy_block_is_blocked_and_not_approvable(ctx):
    client, _ = ctx
    t = _mission(client, instruction="Buy a gift card with bitcoin", budget=100)
    assert t["decision"] == "BLOCK"
    assert t["status"] == "BLOCKED"
    assert t["human_approval"] == "NOT_APPLICABLE"
    r = client.post(f"/api/transactions/{t['id']}/approve")
    assert r.status_code == 400
    r = client.post("/api/transactions/execute", json={"transaction_id": t["id"]})
    assert r.status_code == 403


def test_creation_audit_has_no_human_event_and_lands_pending(ctx):
    client, Session = ctx
    t = _mission(client)
    ev = _events(Session, t["id"])
    assert [e[1] for e in ev] == ["PROPOSAL", "RISK_EVALUATED", "POLICY_DECISION"]
    assert ev[-1][0] == lifecycle.POLICY_ENGINE
    assert ev[-1][3] == "AWAITING_APPROVAL"
    assert not any(e[3] == "APPROVED" for e in ev)


def test_stored_reasoning_never_says_auto_approved(ctx):
    client, _ = ctx
    t = _mission(client)
    assert "Auto-approved" not in t["reasoning"]


# ---------------------------------------------------------------- approval

def test_execute_rejected_before_human_approval(ctx):
    client, Session = ctx
    t = _mission(client)
    r = client.post("/api/transactions/execute", json={"transaction_id": t["id"]})
    assert r.status_code == 403
    s = Session()
    assert s.get(Transaction, t["id"]).paypal_order_id is None
    s.close()


def test_human_approval_sets_approved_and_keeps_recommendation(ctx):
    client, Session = ctx
    t = _mission(client, threshold=20)
    r = client.post(f"/api/transactions/{t['id']}/approve")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "APPROVED"
    assert body["human_approval"] == "APPROVED"
    assert body["decision"] == "NEEDS_APPROVAL"   # recommendation unchanged
    last = _events(Session, t["id"])[-1]
    assert last == (lifecycle.USER, "HUMAN_APPROVED", "AWAITING_APPROVAL", "APPROVED")


def test_double_approval_rejected(ctx):
    client, _ = ctx
    t = _mission(client)
    assert client.post(f"/api/transactions/{t['id']}/approve").status_code == 200
    assert client.post(f"/api/transactions/{t['id']}/approve").status_code == 400


def test_api_list_and_detail_agree(ctx):
    client, _ = ctx
    t = _mission(client)
    listed = next(x for x in client.get("/api/transactions").json() if x["id"] == t["id"])
    detail = client.get(f"/api/transactions/{t['id']}").json()
    for view in (listed, detail):
        assert view["status"] == "AWAITING_APPROVAL"
        assert view["decision"] == "APPROVE"
        assert view["human_approval"] == "PENDING"


# ------------------------------------------------------------ state machine

def test_approved_not_an_initial_state():
    assert "APPROVED" not in lifecycle.INITIAL_STATES


@pytest.mark.parametrize("src", ["PENDING", "BLOCKED", "FAILED", "COMPLETED", "PAYPAL_CREATED"])
def test_approved_only_reachable_from_awaiting(src):
    assert lifecycle.can_transition(src, "APPROVED") is False
    assert lifecycle.can_transition("AWAITING_APPROVAL", "APPROVED") is True


def test_cannot_jump_from_pending_approval_to_paid():
    assert lifecycle.can_transition("AWAITING_APPROVAL", "PAYPAL_CREATED") is False
    assert lifecycle.can_transition("AWAITING_APPROVAL", "COMPLETED") is False


@pytest.mark.parametrize("status,expected", [
    ("AWAITING_APPROVAL", "PENDING"),
    ("APPROVED", "APPROVED"),
    ("PAYPAL_CREATED", "APPROVED"),
    ("COMPLETED", "APPROVED"),
    ("BLOCKED", "NOT_APPLICABLE"),
    ("FAILED", "NOT_APPLICABLE"),
])
def test_human_approval_state_mapping(status, expected):
    assert lifecycle.human_approval_state(status) == expected


# ------------------------------------------------------- payment completion

def test_completed_only_after_paypal_capture_completed(ctx, monkeypatch):
    client, Session = ctx
    _patch_paypal_ok(monkeypatch, capture_status="COMPLETED")
    t = _mission(client)
    # Capture before an order exists is rejected.
    assert client.post("/api/transactions/capture",
                       json={"transaction_id": t["id"]}).status_code == 400
    assert client.post(f"/api/transactions/{t['id']}/approve").status_code == 200
    r = client.post("/api/transactions/execute", json={"transaction_id": t["id"]})
    assert r.status_code == 200 and r.json()["status"] == "PAYPAL_CREATED"
    r = client.post("/api/transactions/capture", json={"transaction_id": t["id"]})
    assert r.status_code == 200
    assert r.json()["status"] == "COMPLETED"
    assert r.json()["paypal_capture_id"] == "CAP-TEST"
    states = [e[3] for e in _events(Session, t["id"]) if e[3]]
    assert states == ["AWAITING_APPROVAL", "APPROVED", "PAYPAL_CREATED", "COMPLETED"]


def test_pending_capture_is_not_completed_and_not_failed(ctx, monkeypatch):
    """A PENDING capture keeps PAYPAL_CREATED (webhook completes it later)."""
    client, _ = ctx
    _patch_paypal_ok(monkeypatch, capture_status="PENDING")
    t = _mission(client)
    client.post(f"/api/transactions/{t['id']}/approve")
    client.post("/api/transactions/execute", json={"transaction_id": t["id"]})
    r = client.post("/api/transactions/capture", json={"transaction_id": t["id"]})
    assert r.json()["status"] == "PAYPAL_CREATED"


def test_declined_capture_marks_failed_not_completed(ctx, monkeypatch):
    client, _ = ctx
    _patch_paypal_ok(monkeypatch, capture_status="DECLINED")
    t = _mission(client)
    client.post(f"/api/transactions/{t['id']}/approve")
    client.post("/api/transactions/execute", json={"transaction_id": t["id"]})
    r = client.post("/api/transactions/capture", json={"transaction_id": t["id"]})
    assert r.json()["status"] == "FAILED"


# ------------------------------------------------------- orchestrator handoff

def test_orchestrator_handoff_preserves_recommendation(ctx):
    """Dry run and handoff use the same budget + threshold -> same decision,
    and the created transaction still waits for a human."""
    from app.orchestrator.orchestrator import OrchestrateRequest, run_orchestration

    client, Session = ctx
    for threshold, expected in ((200, "APPROVE"), (20, "NEEDS_APPROVAL")):
        db = Session()
        try:
            dry = asyncio.run(run_orchestration(db, OrchestrateRequest(
                instruction=KEYBOARD, approval_threshold=threshold)))
        finally:
            db.close()
        c = dry.steps[0].output["constraints"]
        assert dry.final_decision == expected
        t = _mission(client, budget=c["max_budget"], threshold=c["approval_threshold"])
        assert t["decision"] == dry.final_decision
        assert t["status"] == "AWAITING_APPROVAL"
        assert dry.payment_executed is False
