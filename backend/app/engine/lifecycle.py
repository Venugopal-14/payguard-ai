"""Transaction state machine + audit recording.

Centralizes every transaction state change so transitions are validated and
each one produces an immutable audit event. Nothing here stores secrets.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.core import metrics
from app.models.audit_event import AuditEvent
from app.models.transaction import Transaction

logger = logging.getLogger("payguard.lifecycle")

# Actors.
AI_AGENT = "AI_AGENT"
RISK_ENGINE = "RISK_ENGINE"
POLICY_ENGINE = "POLICY_ENGINE"
USER = "USER"
PAYPAL = "PAYPAL"
PAYPAL_WEBHOOK = "PAYPAL_WEBHOOK"
RECONCILER = "RECONCILER"
SYSTEM = "SYSTEM"

# Allowed transitions. Keys are source states; values are permitted targets.
ALLOWED: dict[str, set[str]] = {
    "PENDING": {"APPROVED", "AWAITING_APPROVAL", "BLOCKED", "FAILED"},
    "AWAITING_APPROVAL": {"APPROVED", "BLOCKED", "FAILED"},
    "APPROVED": {"PAYPAL_CREATED", "FAILED"},
    "PAYPAL_CREATED": {"COMPLETED", "FAILED"},
    # terminal states
    "COMPLETED": set(),
    "BLOCKED": set(),
    "FAILED": set(),
}

# Statuses that create_mission assigns directly (initial states), allowed as a
# transition from the implicit "new" origin.
INITIAL_STATES = {"APPROVED", "AWAITING_APPROVAL", "BLOCKED"}


class InvalidTransition(ValueError):
    pass


def can_transition(current: str, target: str) -> bool:
    if current == target:
        return True  # idempotent no-op is allowed
    return target in ALLOWED.get(current, set())


def record_event(
    db: Session,
    *,
    transaction_id: int | None,
    correlation_id: str | None,
    event_type: str,
    actor: str,
    previous_state: str | None = None,
    new_state: str | None = None,
    reason: str = "",
    metadata: dict | None = None,
    commit: bool = True,
) -> AuditEvent:
    """Append an immutable audit event. Metadata is sanitized of secrets."""
    safe_meta = _sanitize(metadata) if metadata else None
    ev = AuditEvent(
        transaction_id=transaction_id,
        correlation_id=correlation_id,
        event_type=event_type,
        actor=actor,
        previous_state=previous_state,
        new_state=new_state,
        reason=reason[:2000],
        event_metadata=json.dumps(safe_meta) if safe_meta is not None else None,
    )
    db.add(ev)
    if commit:
        db.commit()
    metrics.audit_events_total.labels(actor, event_type).inc()
    return ev


def transition(
    db: Session,
    txn: Transaction,
    *,
    target: str,
    actor: str,
    reason: str = "",
    event_type: str | None = None,
    metadata: dict | None = None,
) -> AuditEvent:
    """Validate + apply a state change and record an audit event.

    Raises InvalidTransition on a disallowed change (state is left untouched and
    a SYSTEM error audit event is recorded).
    """
    current = txn.status
    if not can_transition(current, target):
        record_event(
            db,
            transaction_id=txn.id,
            correlation_id=txn.correlation_id,
            event_type="INVALID_TRANSITION",
            actor=SYSTEM,
            previous_state=current,
            new_state=target,
            reason=f"Rejected transition {current} -> {target}",
        )
        raise InvalidTransition(f"{current} -> {target} is not allowed")

    previous = current
    if current != target:
        txn.status = target
    ev = record_event(
        db,
        transaction_id=txn.id,
        correlation_id=txn.correlation_id,
        event_type=event_type or f"STATE_{target}",
        actor=actor,
        previous_state=previous,
        new_state=target,
        reason=reason,
        metadata=metadata,
        commit=False,
    )
    db.commit()
    db.refresh(txn)
    return ev


_SECRET_KEYS = {
    "secret", "client_secret", "access_token", "token", "authorization",
    "signature", "password", "paypal_client_secret",
}


def _sanitize(meta: dict) -> dict:
    out: dict = {}
    for k, v in meta.items():
        if any(s in k.lower() for s in _SECRET_KEYS):
            out[k] = "[redacted]"
        else:
            out[k] = v
    return out
