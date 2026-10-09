"""Phase 4A webhook tests.

Uses an in-memory SQLite DB and mocks PayPal signature verification so no real
PayPal calls or transactions occur.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.models.transaction import Transaction
from app.models.webhook_event import WebhookEvent
from app.payments.webhooks import process_event


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _txn(db, order_id="ORDER123", status="PAYPAL_CREATED"):
    t = Transaction(
        description="USB cable", merchant="Amazon", amount=20, currency="USD",
        decision="APPROVE", risk_score=10, risk_level="LOW", reasoning="",
        status=status, paypal_order_id=order_id,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _resource(order_id="ORDER123", capture_id="CAP999"):
    return {
        "id": capture_id,
        "supplementary_data": {"related_ids": {"order_id": order_id}},
    }


def test_capture_completed_marks_completed(db):
    t = _txn(db)
    res = process_event(
        db, event_id="EVT-1", event_type="PAYMENT.CAPTURE.COMPLETED",
        resource=_resource(),
    )
    db.refresh(t)
    assert t.status == "COMPLETED"
    assert t.paypal_capture_id == "CAP999"
    assert "COMPLETED" in res


def test_capture_denied_marks_failed(db):
    t = _txn(db)
    process_event(
        db, event_id="EVT-2", event_type="PAYMENT.CAPTURE.DENIED",
        resource=_resource(),
    )
    db.refresh(t)
    assert t.status == "FAILED"


def test_capture_pending_records_event(db):
    t = _txn(db, status="PAYPAL_CREATED")
    res = process_event(
        db, event_id="EVT-3", event_type="PAYMENT.CAPTURE.PENDING",
        resource=_resource(),
    )
    db.refresh(t)
    # Pending does not force a state change; it is recorded for audit.
    assert "pending" in res.lower()


def test_order_approved_records_event(db):
    _txn(db)
    res = process_event(
        db, event_id="EVT-4", event_type="CHECKOUT.ORDER.APPROVED",
        resource={"id": "ORDER123", "purchase_units": []},
    )
    assert "approved" in res.lower()


def test_approval_reversed_marks_failed(db):
    t = _txn(db)
    process_event(
        db, event_id="EVT-5", event_type="CHECKOUT.PAYMENT-APPROVAL.REVERSED",
        resource=_resource(),
    )
    db.refresh(t)
    assert t.status == "FAILED"


def test_duplicate_event_processed_once(db):
    t = _txn(db)
    process_event(db, event_id="EVT-DUP", event_type="PAYMENT.CAPTURE.COMPLETED", resource=_resource())
    db.refresh(t)
    assert t.status == "COMPLETED"
    # Now flip status manually and resend the same event id -> must be ignored.
    t.status = "APPROVED"
    db.commit()
    res = process_event(db, event_id="EVT-DUP", event_type="PAYMENT.CAPTURE.COMPLETED", resource=_resource())
    db.refresh(t)
    assert res == "duplicate"
    assert t.status == "APPROVED"  # unchanged by the duplicate
    count = db.query(WebhookEvent).filter(WebhookEvent.paypal_event_id == "EVT-DUP").count()
    assert count == 1


def test_unknown_transaction_safely_handled(db):
    res = process_event(
        db, event_id="EVT-6", event_type="PAYMENT.CAPTURE.COMPLETED",
        resource=_resource(order_id="DOES_NOT_EXIST"),
    )
    assert res == "no_matching_transaction"


def test_unknown_event_ignored(db):
    _txn(db)
    res = process_event(
        db, event_id="EVT-7", event_type="SOME.RANDOM.EVENT",
        resource=_resource(),
    )
    assert res == "unsupported_event_ignored"
