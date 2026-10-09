"""Phase 4B tests: state machine, audit events, correlation, sanitization."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.engine import lifecycle
from app.models.audit_event import AuditEvent
from app.models.transaction import Transaction
from app.models.webhook_event import WebhookEvent  # noqa: F401  (ensure table)
from app.payments.webhooks import process_event


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _txn(db, status="APPROVED", order_id="ORDER123", corr="corr-1"):
    t = Transaction(
        description="USB cable", merchant="Amazon", amount=20, currency="USD",
        decision="APPROVE", risk_score=10, risk_level="LOW", reasoning="",
        status=status, paypal_order_id=order_id, correlation_id=corr,
    )
    db.add(t); db.commit(); db.refresh(t)
    return t


def test_valid_transition():
    assert lifecycle.can_transition("APPROVED", "PAYPAL_CREATED")
    assert lifecycle.can_transition("PAYPAL_CREATED", "COMPLETED")


def test_invalid_transition():
    assert not lifecycle.can_transition("COMPLETED", "PENDING")
    assert not lifecycle.can_transition("BLOCKED", "COMPLETED")


def test_transition_records_audit(db):
    t = _txn(db, status="APPROVED")
    lifecycle.transition(db, t, target="PAYPAL_CREATED", actor=lifecycle.PAYPAL,
                         event_type="ORDER_CREATED", reason="created")
    db.refresh(t)
    assert t.status == "PAYPAL_CREATED"
    events = db.query(AuditEvent).filter(AuditEvent.transaction_id == t.id).all()
    assert any(e.new_state == "PAYPAL_CREATED" for e in events)


def test_invalid_transition_rejected_and_recorded(db):
    t = _txn(db, status="COMPLETED")
    with pytest.raises(lifecycle.InvalidTransition):
        lifecycle.transition(db, t, target="PENDING", actor=lifecycle.SYSTEM)
    db.refresh(t)
    assert t.status == "COMPLETED"  # unchanged
    bad = db.query(AuditEvent).filter(AuditEvent.event_type == "INVALID_TRANSITION").all()
    assert len(bad) == 1


def test_webhook_creates_audit_event(db):
    t = _txn(db, status="PAYPAL_CREATED")
    process_event(db, event_id="W-1", event_type="PAYMENT.CAPTURE.COMPLETED",
                  resource={"id": "CAP1", "supplementary_data": {"related_ids": {"order_id": "ORDER123"}}})
    db.refresh(t)
    assert t.status == "COMPLETED"
    events = db.query(AuditEvent).filter(AuditEvent.transaction_id == t.id).all()
    assert any(e.actor == lifecycle.RECONCILER for e in events)


def test_duplicate_webhook_no_duplicate_audit(db):
    t = _txn(db, status="PAYPAL_CREATED")
    res_body = {"id": "CAP1", "supplementary_data": {"related_ids": {"order_id": "ORDER123"}}}
    process_event(db, event_id="W-DUP", event_type="PAYMENT.CAPTURE.COMPLETED", resource=res_body)
    before = db.query(AuditEvent).count()
    r = process_event(db, event_id="W-DUP", event_type="PAYMENT.CAPTURE.COMPLETED", resource=res_body)
    after = db.query(AuditEvent).count()
    assert r == "duplicate"
    assert before == after


def test_correlation_id_preserved(db):
    t = _txn(db, status="PAYPAL_CREATED", corr="trace-xyz")
    process_event(db, event_id="W-2", event_type="PAYMENT.CAPTURE.COMPLETED",
                  resource={"id": "CAP1", "supplementary_data": {"related_ids": {"order_id": "ORDER123"}}})
    ev = db.query(AuditEvent).filter(AuditEvent.actor == lifecycle.RECONCILER).first()
    assert ev is not None
    assert ev.correlation_id == "trace-xyz"


def test_sensitive_data_not_stored(db):
    t = _txn(db)
    lifecycle.record_event(
        db, transaction_id=t.id, correlation_id=t.correlation_id,
        event_type="TEST", actor=lifecycle.SYSTEM,
        metadata={"client_secret": "SHOULD_NOT_APPEAR", "order_id": "OK123"},
    )
    ev = db.query(AuditEvent).filter(AuditEvent.event_type == "TEST").first()
    assert ev.event_metadata is not None
    meta = json.loads(ev.event_metadata)
    assert meta["client_secret"] == "[redacted]"
    assert meta["order_id"] == "OK123"
    assert "SHOULD_NOT_APPEAR" not in ev.event_metadata
