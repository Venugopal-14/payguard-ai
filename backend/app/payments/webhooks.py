"""PayPal webhook event processing + transaction reconciliation.

Kept separate from payment execution. The processor only reconciles our records
with what PayPal reports; it never initiates a capture or payment itself.
"""

from __future__ import annotations

import logging
from datetime import datetime

import time

from sqlalchemy.orm import Session

from app.core import metrics
from app.engine import lifecycle
from app.models.transaction import Transaction
from app.models.webhook_event import WebhookEvent

logger = logging.getLogger("payguard.webhooks")

SUPPORTED_EVENTS = {
    "CHECKOUT.ORDER.APPROVED",
    "PAYMENT.CAPTURE.PENDING",
    "PAYMENT.CAPTURE.COMPLETED",
    "PAYMENT.CAPTURE.DENIED",
    "CHECKOUT.PAYMENT-APPROVAL.REVERSED",
}


def _resource_order_id(resource: dict) -> str | None:
    """Best-effort extraction of the PayPal order id from a webhook resource."""
    # Capture resources carry the order id in supplementary_data.related_ids.
    rel = (resource.get("supplementary_data") or {}).get("related_ids") or {}
    if rel.get("order_id"):
        return rel["order_id"]
    # Order resources carry the id directly.
    if resource.get("id") and resource.get("purchase_units") is not None:
        return resource["id"]
    return None


def _find_transaction(db: Session, resource: dict) -> Transaction | None:
    order_id = _resource_order_id(resource)
    if order_id:
        txn = (
            db.query(Transaction)
            .filter(Transaction.paypal_order_id == order_id)
            .first()
        )
        if txn:
            return txn
    # Fall back to capture id (if we already stored one).
    cap_id = resource.get("id")
    if cap_id:
        txn = (
            db.query(Transaction)
            .filter(Transaction.paypal_capture_id == cap_id)
            .first()
        )
        if txn:
            return txn
    return None


def process_event(db: Session, *, event_id: str, event_type: str, resource: dict) -> str:
    """Reconcile one verified event. Returns a short human-readable result.

    Idempotent: if `event_id` was already recorded, it is not processed again.
    """
    existing = (
        db.query(WebhookEvent)
        .filter(WebhookEvent.paypal_event_id == event_id)
        .first()
    )
    if existing:
        metrics.webhook_duplicates_total.inc()
        logger.info("webhook_duplicate event_id=%s", event_id)
        return "duplicate"

    record = WebhookEvent(
        paypal_event_id=event_id,
        event_type=event_type,
        resource_id=resource.get("id"),
        processed=False,
    )
    db.add(record)
    db.commit()

    result = "ignored"
    start = time.perf_counter()
    try:
        if event_type not in SUPPORTED_EVENTS:
            result = "unsupported_event_ignored"
        else:
            txn = _find_transaction(db, resource)
            if txn is None:
                result = "no_matching_transaction"
            else:
                result = _apply(db, txn, event_type, resource)

        record.processed = True
        record.processed_at = datetime.utcnow()
        db.commit()
        metrics.webhook_events_total.labels(event_type, "processed").inc()
        metrics.webhook_processing_seconds.observe(time.perf_counter() - start)
        logger.info(
            "webhook_processed event_id=%s type=%s result=%s",
            event_id, event_type, result,
        )
    except Exception as exc:  # noqa: BLE001
        record.processing_error = str(exc)[:500]
        db.commit()
        metrics.webhook_events_total.labels(event_type, "error").inc()
        logger.warning("webhook_failed event_id=%s error=%s", event_id, exc)
        raise

    return result


def _reconcile(db: Session, txn: Transaction, target: str, event_type: str, reason: str,
               meta: dict | None = None) -> str:
    """Apply a webhook-driven state change through the audited state machine.

    If the transition is invalid for the current state, record it and skip
    without raising (webhook acks still succeed; state is left intact).
    """
    try:
        lifecycle.transition(
            db, txn, target=target, actor=lifecycle.RECONCILER,
            event_type=event_type, reason=reason, metadata=meta,
        )
        metrics.reconciliation_total.labels("success").inc()
        return f"txn #{txn.id} reconciled -> {target}"
    except lifecycle.InvalidTransition:
        metrics.reconciliation_total.labels("invalid").inc()
        return f"txn #{txn.id} transition {txn.status} -> {target} skipped (invalid)"


def _apply(db: Session, txn: Transaction, event_type: str, resource: dict) -> str:
    if event_type == "CHECKOUT.ORDER.APPROVED":
        # Buyer approved at PayPal. We do NOT auto-capture here. Record only.
        lifecycle.record_event(
            db, transaction_id=txn.id, correlation_id=txn.correlation_id,
            event_type="PAYPAL_ORDER_APPROVED", actor=lifecycle.PAYPAL_WEBHOOK,
            reason="Buyer approved the order at PayPal.",
        )
        return f"order approved; txn #{txn.id} status {txn.status}"

    if event_type == "PAYMENT.CAPTURE.PENDING":
        lifecycle.record_event(
            db, transaction_id=txn.id, correlation_id=txn.correlation_id,
            event_type="PAYPAL_CAPTURE_PENDING", actor=lifecycle.PAYPAL_WEBHOOK,
            reason="Capture pending at PayPal.",
        )
        return f"capture pending; txn #{txn.id}"

    if event_type == "PAYMENT.CAPTURE.COMPLETED":
        if resource.get("id"):
            txn.paypal_capture_id = resource["id"]
            db.commit()
        return _reconcile(db, txn, "COMPLETED", "WEBHOOK_CAPTURE_COMPLETED",
                          "PayPal webhook: capture completed.",
                          {"capture_id": resource.get("id")})

    if event_type == "PAYMENT.CAPTURE.DENIED":
        return _reconcile(db, txn, "FAILED", "WEBHOOK_CAPTURE_DENIED",
                          "PayPal webhook: capture denied.")

    if event_type == "CHECKOUT.PAYMENT-APPROVAL.REVERSED":
        return _reconcile(db, txn, "FAILED", "WEBHOOK_APPROVAL_REVERSED",
                          "PayPal webhook: approval reversed.")

    return "noop"
