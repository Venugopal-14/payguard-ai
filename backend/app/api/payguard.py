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

from app.agent.agent import propose_purchase
from app.db.database import get_db
from app.engine import policy
from app.engine.risk import evaluate_risk
from app.models.mission import Mission
from app.models.transaction import Transaction
from app.payments import paypal
from app.schemas.payment import (
    AgentView,
    CaptureRequest,
    ExecuteRequest,
    MissionRequest,
    ProposeResponse,
    RiskView,
    TransactionView,
)

router = APIRouter(prefix="/api", tags=["payguard"])


_STATUS_FOR_DECISION = {
    policy.APPROVE: "APPROVED",
    policy.NEEDS_APPROVAL: "AWAITING_APPROVAL",
    policy.BLOCK: "BLOCKED",
}


@router.post("/missions", response_model=ProposeResponse)
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

    # 2. Risk engine evaluates.
    risk = evaluate_risk(
        description=proposal.description,
        merchant=proposal.merchant,
        amount=proposal.amount,
        budget=req.budget,
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

    txn = Transaction(
        mission_id=mission.id,
        description=proposal.description,
        merchant=proposal.merchant,
        amount=proposal.amount,
        currency=proposal.currency,
        decision=decision.decision,
        risk_score=risk.score,
        risk_level=risk.level,
        reasoning=reasoning,
        status=_STATUS_FOR_DECISION[decision.decision],
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)

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
        risk=RiskView(score=risk.score, level=risk.level, signals=risk.signals),
        policy_reasons=decision.reasons,
    )


@router.post("/transactions/{transaction_id}/approve", response_model=TransactionView)
def approve_transaction(transaction_id: int, db: Session = Depends(get_db)):
    """Human approves a transaction that was AWAITING_APPROVAL."""
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(404, "Transaction not found.")
    if txn.status != "AWAITING_APPROVAL":
        raise HTTPException(
            400, f"Transaction is {txn.status}, not awaiting approval."
        )
    txn.status = "APPROVED"
    db.commit()
    db.refresh(txn)
    return TransactionView.model_validate(txn)


@router.post("/transactions/execute", response_model=TransactionView)
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
    except Exception as exc:  # noqa: BLE001
        txn.status = "FAILED"
        db.commit()
        raise HTTPException(502, f"PayPal order creation failed: {exc}")

    txn.paypal_order_id = order.get("id")
    txn.approve_url = paypal.extract_approve_url(order)
    txn.status = "PAYPAL_CREATED"
    db.commit()
    db.refresh(txn)
    return TransactionView.model_validate(txn)


@router.post("/transactions/capture", response_model=TransactionView)
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
    except Exception as exc:  # noqa: BLE001
        txn.status = "FAILED"
        db.commit()
        raise HTTPException(502, f"PayPal capture failed: {exc}")

    txn.paypal_capture_id = paypal.extract_capture_id(result)
    txn.status = "COMPLETED" if result.get("status") == "COMPLETED" else "FAILED"
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
