"""Phase 3A tests for the advanced, explainable risk engine."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.engine.risk import (
    CRITICAL,
    HIGH,
    LOW,
    HistoryRecord,
    evaluate_risk,
)


def _r(**kw):
    base = dict(
        description="USB cable", merchant="Amazon", amount=20, budget=500
    )
    base.update(kw)
    return evaluate_risk(**base)


def test_normal_low_risk():
    r = _r()
    assert r.level == LOW
    assert r.recommended_action == "APPROVE"
    assert isinstance(r.factors, list)


def test_large_transaction_increases_risk():
    low = _r(amount=20).score
    big = _r(amount=1500, budget=5000).score
    assert big > low
    assert any(f.factor == "large_transaction" for f in _r(amount=1500, budget=5000).factors)


def test_unknown_merchant_increases_risk():
    r = _r(merchant="randomshop")
    assert any(f.factor == "unknown_merchant" for f in r.factors)
    assert r.score >= 30


def test_blocked_merchant_is_critical():
    r = _r(merchant="silkroad")
    assert r.level in (HIGH, CRITICAL)
    assert r.score >= 85
    assert r.recommended_action == "BLOCK"


def test_blocked_category_is_critical():
    r = _r(category="gambling")
    assert r.level in (HIGH, CRITICAL)
    assert r.recommended_action == "BLOCK"


def test_velocity_anomaly():
    now = datetime.utcnow()
    history = [
        HistoryRecord("Amazon", 10, now - timedelta(minutes=1), "COMPLETED")
        for _ in range(5)
    ]
    r = _r(history=history)
    assert any(f.factor == "velocity_anomaly" for f in r.factors)


def test_new_merchant_plus_large_amount_combines():
    now = datetime.utcnow()
    history = [HistoryRecord("Apple", 10, now - timedelta(days=1), "COMPLETED")]
    r = _r(merchant="Amazon", amount=2000, budget=5000, history=history)
    factor_names = {f.factor for f in r.factors}
    assert "large_transaction" in factor_names
    assert r.score > 15


def test_currency_anomaly():
    r = _r(currency="XYZ")
    assert any(f.factor == "currency_anomaly" for f in r.factors)


def test_score_never_exceeds_100():
    r = _r(merchant="silkroad", amount=99999, budget=1, currency="XYZ",
           category="gambling", description="gift card bitcoin")
    assert r.score <= 100


def test_behavioral_anomaly():
    now = datetime.utcnow()
    history = [
        HistoryRecord("Amazon", 10, now - timedelta(days=i), "COMPLETED")
        for i in range(1, 5)
    ]
    r = _r(amount=500, budget=5000, history=history)
    assert any(f.factor == "behavioral_anomaly" for f in r.factors)


def test_explanation_and_action_present():
    r = _r(merchant="randomshop", amount=400, budget=500)
    assert r.explanation
    assert r.recommended_action in ("APPROVE", "REQUIRE_APPROVAL", "BLOCK")
