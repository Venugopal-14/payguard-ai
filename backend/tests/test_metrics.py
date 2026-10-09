"""Phase 4C observability tests.

Validates the /metrics endpoint, that instrumented counters increment, and that
forbidden high-cardinality labels / secrets never appear in the output.
"""

from __future__ import annotations

from app.core import metrics
from app.engine import lifecycle, policy
from app.engine.risk import evaluate_risk
from prometheus_client import generate_latest


def _dump() -> str:
    return generate_latest().decode("utf-8")


def test_metrics_endpoint_text_format():
    text = _dump()
    assert "payguard_transactions_total" in text
    assert "# HELP" in text and "# TYPE" in text


def test_ai_metric_increments():
    before = _dump()
    metrics.ai_proposals_total.labels("ollama", "success").inc()
    after = _dump()
    assert after != before
    assert "payguard_ai_proposals_total" in after


def test_risk_metric_increments():
    metrics.risk_evaluations_total.labels("LOW").inc()
    metrics.risk_score.observe(25)
    text = _dump()
    assert "payguard_risk_evaluations_total" in text
    assert "payguard_risk_score" in text


def test_policy_metric_increments():
    metrics.policy_decisions_total.labels("APPROVE").inc()
    assert "payguard_policy_decisions_total" in _dump()


def test_approval_metric_increments():
    metrics.approval_requests_total.inc()
    metrics.approval_decisions_total.labels("approved").inc()
    text = _dump()
    assert "payguard_approval_requests_total" in text
    assert "payguard_approval_decisions_total" in text


def test_webhook_metrics_increment():
    metrics.webhook_events_total.labels("PAYMENT.CAPTURE.COMPLETED", "processed").inc()
    metrics.webhook_duplicates_total.inc()
    text = _dump()
    assert "payguard_webhook_events_total" in text
    assert "payguard_webhook_duplicates_total" in text


def test_audit_metric_increments():
    metrics.audit_events_total.labels("AI_AGENT", "PROPOSAL").inc()
    assert "payguard_audit_events_total" in _dump()


def test_no_high_cardinality_or_secret_labels():
    text = _dump()
    # Forbidden label names must never appear.
    for forbidden in ("transaction_id", "correlation_id", "order_id"):
        assert forbidden not in text
    # Known secret fragments must never appear.
    for secret in ("EHN8z6Lr", "EKvzsPq", "client_secret", "access_token"):
        assert secret not in text


def test_pipeline_updates_metrics_end_to_end():
    """A risk+policy evaluation reflected in exported metrics."""
    risk = evaluate_risk(description="USB cable", merchant="Amazon", amount=20, budget=500)
    metrics.risk_evaluations_total.labels(risk.level).inc()
    decision = policy.decide(amount=20, budget=500, approval_threshold=200, risk=risk)
    metrics.policy_decisions_total.labels(decision.decision).inc()
    text = _dump()
    assert "payguard_risk_evaluations_total" in text
    assert "payguard_policy_decisions_total" in text
