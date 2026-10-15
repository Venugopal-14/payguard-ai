"""Phase 3 tests for simulated fraud-prevention analytics."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.engine.analytics import compute_fraud_analytics
from app.models.transaction import Transaction


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _add(db, **kw):
    base = dict(
        description="x", merchant="m", amount=10, currency="USD",
        decision="APPROVE", risk_score=10, risk_level="LOW", reasoning="",
        status="APPROVED",
    )
    base.update(kw)
    db.add(Transaction(**base))
    db.commit()


def _sim(out, ccy="USD"):
    return out["simulated_blocked_amount_by_currency"].get(ccy, 0)


def _pol(out, ccy="USD"):
    return out["policy_blocked_amount_by_currency"].get(ccy, 0)


def test_empty_data():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    out = compute_fraud_analytics(s)
    assert out["total_transactions"] == 0
    assert out["approval_rate_pct"] is None  # shows "—"
    assert out["simulated_blocked_amount_by_currency"] == {}
    s.close()


def test_scam_block_counts_as_simulated_fraud(db):
    _add(db, status="BLOCKED", decision="BLOCK", risk_level="CRITICAL", amount=200,
         reasoning="Risk: Merchant 'unknownvendor' is explicitly blocked.")
    out = compute_fraud_analytics(db)
    assert out["simulated_fraud_attempts_blocked"] == 1
    assert _sim(out) == 200
    assert _pol(out) == 0


def test_budget_block_is_not_fraud(db):
    _add(db, status="BLOCKED", decision="BLOCK", risk_level="HIGH", amount=5000,
         reasoning="Policy: Amount 5000.00 exceeds the budget 150.00.")
    out = compute_fraud_analytics(db)
    assert out["simulated_fraud_attempts_blocked"] == 0
    assert _pol(out) == 5000
    assert _sim(out) == 0


def test_unknown_merchant_only_block_is_not_fraud(db):
    # Unknown merchant alone is a risk signal, NOT simulated fraud.
    _add(db, status="BLOCKED", decision="BLOCK", risk_level="HIGH", amount=400,
         reasoning="Risk: Merchant 'randomshop' is not on the known-merchant list.")
    out = compute_fraud_analytics(db)
    assert out["simulated_fraud_attempts_blocked"] == 0
    assert _sim(out) == 0
    assert _pol(out) == 400


def test_completed_payment_excluded_from_blocked(db):
    _add(db, status="COMPLETED", decision="APPROVE", risk_level="LOW", amount=1.60,
         reasoning="Risk: Merchant 'Amazon' is recognized and trusted.")
    out = compute_fraud_analytics(db)
    assert _sim(out) == 0
    assert _pol(out) == 0


def test_mixed_currencies_not_summed(db):
    # A USD scam block and an EUR scam block must stay in separate buckets.
    _add(db, status="BLOCKED", decision="BLOCK", currency="USD", amount=100,
         reasoning="Risk: suspicious terms detected: gift card.")
    _add(db, status="BLOCKED", decision="BLOCK", currency="EUR", amount=50,
         reasoning="Risk: suspicious terms detected: crypto.")
    out = compute_fraud_analytics(db)
    assert _sim(out, "USD") == 100
    assert _sim(out, "EUR") == 50
    assert out["simulated_fraud_attempts_blocked"] == 2


def test_pending_excluded_from_approval_rate(db):
    _add(db, status="APPROVED", decision="APPROVE")               # decided+approved
    _add(db, status="BLOCKED", decision="BLOCK",
         reasoning="Policy: budget.")                             # decided, not approved
    _add(db, status="AWAITING_APPROVAL", decision="NEEDS_APPROVAL")  # pending
    out = compute_fraud_analytics(db)
    assert out["pending_approval"] == 1
    assert out["decided_transactions"] == 2   # pending excluded
    assert out["approved_transactions"] == 1
    assert out["approval_rate_pct"] == 50.0   # 1/2


def test_distribution_and_breakdown(db):
    _add(db, risk_level="LOW", decision="APPROVE", status="APPROVED")
    _add(db, risk_level="MEDIUM", decision="NEEDS_APPROVAL", status="AWAITING_APPROVAL")
    _add(db, risk_level="CRITICAL", decision="BLOCK", status="BLOCKED",
         reasoning="Risk: suspicious terms detected: gift card.")
    out = compute_fraud_analytics(db)
    assert out["risk_distribution"]["LOW"] == 1
    assert out["risk_distribution"]["MEDIUM"] == 1
    assert out["risk_distribution"]["CRITICAL"] == 1
    assert out["decision_breakdown"]["BLOCK"] == 1


def test_missing_decision_not_counted_as_approved(db):
    _add(db, decision="", status="APPROVED")   # inconsistent -> not decided
    out = compute_fraud_analytics(db)
    assert out["decided_transactions"] == 0
    assert out["approval_rate_pct"] is None


def test_inconsistent_decision_status_not_approved(db):
    # decision BLOCK but status APPROVED (inconsistent) must NOT count as
    # approved. It is decided (BLOCK is a known decision) but not approved.
    _add(db, decision="BLOCK", status="APPROVED", risk_level="HIGH")
    out = compute_fraud_analytics(db)
    assert out["decided_transactions"] == 1
    assert out["approved_transactions"] == 0
    assert out["approval_rate_pct"] == 0.0


def test_suspicious_terms_block_is_fraud(db):
    _add(db, status="BLOCKED", decision="BLOCK", risk_level="HIGH", amount=120,
         reasoning="Risk: Suspicious terms detected: gift card.")
    out = compute_fraud_analytics(db)
    assert out["simulated_fraud_attempts_blocked"] == 1
    assert _sim(out) == 120


def test_label_is_simulated(db):
    _add(db)
    out = compute_fraud_analytics(db)
    assert "SIMULATED" in out["label"]
