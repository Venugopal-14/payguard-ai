"""PayGuard control-plane API.

The full flow:

  1. POST /api/missions          -> agent proposes a purchase, engines decide,
                                     transaction is persisted with a decision.
  2. POST /api/transactions/execute
                                  -> for APPROVE or human-approved NEEDS_APPROVAL,
                                     create the PayPal order and return approve_url.
  3. (buyer approves in PayPal sandbox using the approve_url)
  4. POST /api/transactions/capture
                                  -> capture the funds and mark COMPLETED.

BLOCK decisions can never create a PayPal order.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import logging
import uuid

from app.agent.agent import propose_purchase
from app.core import metrics
from app.core.security import require_api_key
from app.db.database import get_db
from app.engine import lifecycle, policy
from app.engine.analytics import compute_fraud_analytics
from app.engine.risk import HistoryRecord, evaluate_risk
from app.models.audit_event import AuditEvent
from app.models.mission import Mission
from app.models.transaction import Transaction
from app.payments import paypal
from app.schemas.payment import (
    AgentView,
    AuditEventView,
    CaptureRequest,
    ExecuteRequest,
    MissionRequest,
    ProposeResponse,
    RiskFactorView,
    RiskView,
    TransactionView,
)

router = APIRouter(prefix="/api", tags=["payguard"])

logger = logging.getLogger("payguard.api")


_STATUS_FOR_DECISION = {
    policy.APPROVE: "APPROVED",
    policy.NEEDS_APPROVAL: "AWAITING_APPROVAL",
    policy.BLOCK: "BLOCKED",
}


@router.post(
    "/missions",
    response_model=ProposeResponse,
    dependencies=[Depends(require_api_key)],
)
async def create_mission(req: MissionRequest, db: Session = Depends(get_db)):
    """Agent proposes a purchase for the mission, engines decide its fate."""
    mission = Mission(
        instruction=req.instruction,
        budget=req.budget,
        currency=req.currency.upper(),
        approval_threshold=req.approval_threshold,
    )
    db.add(mission)
    db.commit()
    db.refresh(mission)

    # 1. Agent proposes.
    proposal = await propose_purchase(
        instruction=req.instruction,
        budget=req.budget,
        currency=req.currency.upper(),
    )

    # 2. Risk engine evaluates (with behavioral history for velocity/baseline).
    prior = (
        db.query(Transaction).order_by(Transaction.created_at.desc()).limit(50).all()
    )
    history = [
        HistoryRecord(
            merchant=t.merchant,
            amount=float(t.amount),
            created_at=t.created_at,
            status=t.status,
        )
        for t in prior
    ]
    risk = evaluate_risk(
        description=proposal.description,
        merchant=proposal.merchant,
        amount=proposal.amount,
        budget=req.budget,
        currency=proposal.currency,
        category=proposal.category,
        history=history,
    )

    # 3. Policy engine decides.
    decision = policy.decide(
        amount=proposal.amount,
        budget=req.budget,
        approval_threshold=req.approval_threshold,
        risk=risk,
    )

    reasoning_lines = [f"Agent ({proposal.source}): {proposal.agent_notes}"]
    reasoning_lines += [f"Risk: {s}" for s in risk.signals]
    reasoning_lines += [f"Policy: {r}" for r in decision.reasons]
    reasoning = "\n".join(reasoning_lines)

    correlation_id = uuid.uuid4().hex
    initial_status = _STATUS_FOR_DECISION[decision.decision]
    txn = Transaction(
        mission_id=mission.id,
        correlation_id=correlation_id,
        description=proposal.description,
        merchant=proposal.merchant,
        amount=proposal.amount,
        currency=proposal.currency,
        decision=decision.decision,
        risk_score=risk.score,
        risk_level=risk.level,
        reasoning=reasoning,
        status=initial_status,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)

    # Metrics.
    metrics.transactions_total.labels(decision.decision, initial_status).inc()
    metrics.transaction_value_total.labels(proposal.currency).inc(float(proposal.amount))
    metrics.risk_evaluations_total.labels(risk.level).inc()
    metrics.risk_score.observe(float(risk.score))
    metrics.policy_decisions_total.labels(decision.decision).inc()
    if decision.decision == policy.NEEDS_APPROVAL:
        metrics.approval_requests_total.inc()

    # Audit the decision pipeline (AI -> Risk -> Policy -> initial state).
    lifecycle.record_event(
        db, transaction_id=txn.id, correlation_id=correlation_id,
        event_type="PROPOSAL", actor=lifecycle.AI_AGENT,
        reason=proposal.agent_notes,
        metadata={"provider": proposal.provider, "confidence": proposal.confidence},
        commit=False,
    )
    lifecycle.record_event(
        db, transaction_id=txn.id, correlation_id=correlation_id,
        event_type="RISK_EVALUATED", actor=lifecycle.RISK_ENGINE,
        reason=risk.explanation,
        metadata={"score": risk.score, "level": risk.level},
        commit=False,
    )
    lifecycle.record_event(
        db, transaction_id=txn.id, correlation_id=correlation_id,
        event_type="POLICY_DECISION", actor=lifecycle.POLICY_ENGINE,
        new_state=initial_status,
        reason="; ".join(decision.reasons),
        commit=False,
    )
    db.commit()

    return ProposeResponse(
        transaction=TransactionView.model_validate(txn),
        agent_source=proposal.source,
        agent=AgentView(
            source=proposal.source,
            provider=proposal.provider,
            intent=proposal.intent,
            category=proposal.category,
            confidence=proposal.confidence,
            summary=proposal.agent_notes,
        ),
        risk=RiskView(
            score=risk.score,
            level=risk.level,
            signals=risk.signals,
            factors=[
                RiskFactorView(
                    factor=f.factor, weight=f.weight, description=f.description
                )
                for f in risk.factors
            ],
            explanation=risk.explanation,
            recommended_action=risk.recommended_action,
        ),
        policy_reasons=decision.reasons,
    )


@router.post(
    "/transactions/{transaction_id}/approve",
    response_model=TransactionView,
    dependencies=[Depends(require_api_key)],
)
def approve_transaction(transaction_id: int, db: Session = Depends(get_db)):
    """Human approves a transaction that was AWAITING_APPROVAL."""
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(404, "Transaction not found.")
    if txn.status != "AWAITING_APPROVAL":
        raise HTTPException(
            400, f"Transaction is {txn.status}, not awaiting approval."
        )
    lifecycle.transition(
        db, txn, target="APPROVED", actor=lifecycle.USER,
        event_type="HUMAN_APPROVED", reason="Human approved the transaction.",
    )
    metrics.approval_decisions_total.labels("approved").inc()
    return TransactionView.model_validate(txn)


@router.post(
    "/transactions/execute",
    response_model=TransactionView,
    dependencies=[Depends(require_api_key)],
)
async def execute_transaction(req: ExecuteRequest, db: Session = Depends(get_db)):
    """Create the PayPal order for an APPROVED transaction."""
    txn = db.get(Transaction, req.transaction_id)
    if txn is None:
        raise HTTPException(404, "Transaction not found.")

    if txn.status == "BLOCKED":
        raise HTTPException(403, "This transaction was blocked by policy.")
    if txn.status == "AWAITING_APPROVAL":
        raise HTTPException(403, "Human approval is required before execution.")
    if txn.status not in ("APPROVED",):
        raise HTTPException(400, f"Cannot execute a transaction in {txn.status}.")

    try:
        order = await paypal.create_order(
            amount=float(txn.amount),
            currency=txn.currency,
            description=txn.description,
        )
    except paypal.PayPalNotConfigured as exc:
        raise HTTPException(503, str(exc))
    except Exception:  # noqa: BLE001
        metrics.paypal_orders_total.labels("error").inc()
        lifecycle.transition(db, txn, target="FAILED", actor=lifecycle.PAYPAL,
                             event_type="ORDER_FAILED", reason="PayPal order creation failed.")
        # Log full detail server-side (with txn id); return a generic message so
        # internal/PayPal exception text never reaches the client.
        logger.warning("paypal_order_failed txn_id=%s", txn.id, exc_info=True)
        raise HTTPException(502, "PayPal order creation failed.")

    metrics.paypal_orders_total.labels("success").inc()
    txn.paypal_order_id = order.get("id")
    txn.approve_url = paypal.extract_approve_url(order)
    lifecycle.transition(
        db, txn, target="PAYPAL_CREATED", actor=lifecycle.PAYPAL,
        event_type="ORDER_CREATED", reason="PayPal order created.",
        metadata={"order_id": order.get("id")},
    )
    return TransactionView.model_validate(txn)


@router.post(
    "/transactions/capture",
    response_model=TransactionView,
    dependencies=[Depends(require_api_key)],
)
async def capture_transaction(req: CaptureRequest, db: Session = Depends(get_db)):
    """Capture funds after the sandbox buyer approved the order."""
    txn = db.get(Transaction, req.transaction_id)
    if txn is None:
        raise HTTPException(404, "Transaction not found.")
    if not txn.paypal_order_id:
        raise HTTPException(400, "No PayPal order to capture.")
    if txn.status not in ("PAYPAL_CREATED",):
        raise HTTPException(400, f"Cannot capture a transaction in {txn.status}.")

    try:
        result = await paypal.capture_order(txn.paypal_order_id)
    except Exception:  # noqa: BLE001
        metrics.paypal_captures_total.labels("error").inc()
        lifecycle.transition(db, txn, target="FAILED", actor=lifecycle.PAYPAL,
                             event_type="CAPTURE_FAILED", reason="PayPal capture failed.")
        # Log full detail server-side (with txn id); return a generic message so
        # internal/PayPal exception text never reaches the client.
        logger.warning("paypal_capture_failed txn_id=%s", txn.id, exc_info=True)
        raise HTTPException(502, "PayPal capture failed.")

    txn.paypal_capture_id = paypal.extract_capture_id(result)
    _target = "COMPLETED" if result.get("status") == "COMPLETED" else "FAILED"
    metrics.paypal_captures_total.labels(
        "success" if _target == "COMPLETED" else "failed"
    ).inc()
    lifecycle.transition(
        db, txn, target=_target, actor=lifecycle.PAYPAL,
        event_type="CAPTURE_RESULT", reason=f"Capture returned {result.get('status')}.",
        metadata={"capture_id": txn.paypal_capture_id},
    )
    db.commit()
    db.refresh(txn)
    return TransactionView.model_validate(txn)


@router.get("/transactions", response_model=list[TransactionView])
def list_transactions(db: Session = Depends(get_db)):
    txns = (
        db.query(Transaction).order_by(Transaction.created_at.desc()).all()
    )
    return [TransactionView.model_validate(t) for t in txns]


@router.get("/transactions/{transaction_id}", response_model=TransactionView)
def get_transaction(transaction_id: int, db: Session = Depends(get_db)):
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(404, "Transaction not found.")
    return TransactionView.model_validate(txn)


@router.get("/analytics/fraud")
def fraud_analytics(db: Session = Depends(get_db)):
    """Read-only simulated fraud-prevention analytics from persisted data.

    All figures are SIMULATED (clearly labeled). Real completed payments are
    excluded from blocked/saved amounts.
    """
    return compute_fraud_analytics(db)


@router.get("/transactions/{transaction_id}/audit", response_model=list[AuditEventView])
def get_transaction_audit(transaction_id: int, db: Session = Depends(get_db)):
    """Chronological audit timeline for a transaction."""
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(404, "Transaction not found.")
    events = (
        db.query(AuditEvent)
        .filter(AuditEvent.transaction_id == transaction_id)
        .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
        .all()
    )
    return [AuditEventView.model_validate(e) for e in events]
