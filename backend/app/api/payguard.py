"""PayGuard control-plane API.

The full flow:

  1. POST /api/missions          -> agent proposes a purchase, engines decide,
                                     transaction is persisted with a decision.
  1b. POST /api/transactions/{id}/approve
                                  -> a human explicitly approves (AWAITING_APPROVAL
                                     -> APPROVED). The policy recommendation alone
                                     never approves a payment.
  2. POST /api/transactions/execute
                                  -> for human-APPROVED transactions only,
                                     create the PayPal order and return approve_url.
  3. (buyer approves in PayPal sandbox using the approve_url)
  4. POST /api/transactions/capture
                                  -> capture the funds and mark COMPLETED.

BLOCK decisions can never create a PayPal order.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import asyncio
import logging
import uuid
from decimal import Decimal, InvalidOperation

import httpx

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
    CheckoutResult,
    ExecuteRequest,
    PayPalReturnRequest,
    MissionRequest,
    ProposeResponse,
    RiskFactorView,
    RiskView,
    TransactionView,
)

router = APIRouter(prefix="/api", tags=["payguard"])

logger = logging.getLogger("payguard.api")


# The policy decision is a RECOMMENDATION stored in `decision`. It never
# authorizes a payment on its own: every non-blocked transaction starts in
# AWAITING_APPROVAL (shown as "Pending approval") and only becomes APPROVED via
# an explicit human approval (POST /api/transactions/{id}/approve).
_STATUS_FOR_DECISION = {
    policy.APPROVE: "AWAITING_APPROVAL",
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
    # Policy output is a recommendation; never word it as payment authorization.
    reasoning_lines += [
        f"Policy: {r.replace('Auto-approved.', 'Meets policy criteria for approval.')}"
        for r in decision.reasons
    ]
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
    if initial_status == "AWAITING_APPROVAL":
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
        reason=(
            f"Policy recommendation: {decision.decision}. "
            + "; ".join(r.replace("Auto-approved.", "Meets policy criteria for approval.")
                        for r in decision.reasons)
            + (" Human approval required before any payment."
               if initial_status == "AWAITING_APPROVAL" else "")
        ),
        metadata={"policy_recommendation": decision.decision},
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


# ------------------------------------------------------------------ checkout
# Per-transaction locks serialize execute/capture/return for the same
# transaction inside this process (double-clicks, return-page refreshes).
# Across processes/retries, stable PayPal-Request-Id values make PayPal itself
# return the original order/capture instead of creating a second one.
_txn_locks: dict[int, asyncio.Lock] = {}


def _lock_for(txn_id: int) -> asyncio.Lock:
    lock = _txn_locks.get(txn_id)
    if lock is None:
        lock = _txn_locks[txn_id] = asyncio.Lock()
    return lock


def _idem_key(txn: Transaction, kind: str) -> str:
    # correlation_id is a random uuid per transaction, so keys never collide
    # across environments or database resets that reuse integer ids.
    base = txn.correlation_id or f"{txn.id}-{int(txn.created_at.timestamp())}"
    return f"payguard-{kind}-{base}"[:100]


def _is_retryable(exc: Exception) -> bool:
    return isinstance(exc, httpx.TransportError) or (
        isinstance(exc, paypal.PayPalAPIError) and exc.retryable
    )


def _retry_url(txn: Transaction) -> str | None:
    return txn.approve_url if paypal.is_sandbox_approve_url(txn.approve_url) else None


def _result(txn: Transaction, outcome: str, message: str,
            retry_url: str | None = None) -> CheckoutResult:
    return CheckoutResult(
        outcome=outcome, message=message,
        transaction=TransactionView.model_validate(txn), retry_url=retry_url,
    )


def _safe_transition(db: Session, txn: Transaction, **kw) -> None:
    """Transition, tolerating a concurrent change (e.g. a webhook got there first)."""
    try:
        lifecycle.transition(db, txn, **kw)
    except lifecycle.InvalidTransition:
        db.refresh(txn)


def _amount_matches(txn: Transaction, amount: dict | None) -> bool:
    if not amount:
        return False
    try:
        got = Decimal(str(amount.get("value"))).quantize(Decimal("0.01"))
        want = Decimal(str(txn.amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return False
    return got == want and (amount.get("currency_code") or "").upper() == txn.currency.upper()


def _verify_capture(txn: Transaction, resp: dict) -> tuple[str, dict | None, str]:
    """Check PayPal's actual response before trusting it.

    Returns (verdict, capture, reason); verdict is COMPLETED | PENDING | FAILED |
    MISMATCH | UNKNOWN. COMPLETED requires: same order id, order status
    COMPLETED, capture status COMPLETED, and matching amount + currency.
    """
    if resp.get("id") and resp.get("id") != txn.paypal_order_id:
        return "MISMATCH", None, "PayPal returned a different order id."
    cap = paypal.extract_capture(resp)
    if not cap:
        return "UNKNOWN", None, "PayPal response contained no capture."
    if not _amount_matches(txn, cap.get("amount")):
        return "MISMATCH", cap, "Captured amount/currency does not match the transaction."
    status = cap.get("status")
    if status == "COMPLETED" and resp.get("status") == "COMPLETED":
        return "COMPLETED", cap, "PayPal confirmed the capture as COMPLETED."
    if status == "PENDING":
        return "PENDING", cap, "PayPal reports the capture as PENDING."
    if status in ("DECLINED", "FAILED"):
        return "FAILED", cap, f"PayPal reports the capture as {status}."
    return "UNKNOWN", cap, f"Unexpected capture status {status!r}."


async def _capture_verified(db: Session, txn: Transaction) -> tuple[CheckoutResult, bool]:
    """Capture + verify. Caller must hold _lock_for(txn.id).

    Returns (result, is_internal_error). COMPLETED is only ever set after
    _verify_capture confirms PayPal's response. Recoverable cases keep the
    transaction in PAYPAL_CREATED so the buyer/operator can retry.
    """
    db.refresh(txn)
    if txn.status == "COMPLETED":
        return _result(txn, "ALREADY_COMPLETED",
                       "Payment was already captured and confirmed by PayPal."), False
    if txn.status != "PAYPAL_CREATED" or not txn.paypal_order_id:
        return _result(txn, "NOT_CAPTURABLE",
                       f"Transaction is {txn.status}; there is nothing to capture."), False

    corr, order_id = txn.correlation_id, txn.paypal_order_id

    def audit(event_type: str, reason: str, meta: dict | None = None) -> None:
        lifecycle.record_event(
            db, transaction_id=txn.id, correlation_id=corr, event_type=event_type,
            actor=lifecycle.PAYPAL, reason=reason,
            metadata={"order_id": order_id, **(meta or {})},
        )

    try:
        resp = await paypal.capture_order(order_id, request_id=_idem_key(txn, "capture"))
    except (paypal.PayPalNotConfigured, paypal.PayPalNotSandbox) as exc:
        raise HTTPException(503, str(exc))
    except paypal.PayPalAPIError as exc:
        logger.warning("paypal_capture_error txn_id=%s status=%s issue=%s debug_id=%s",
                       txn.id, exc.status_code, exc.issue, exc.debug_id)
        if exc.issue == "ORDER_NOT_APPROVED":
            metrics.paypal_captures_total.labels("not_approved").inc()
            audit("CAPTURE_NOT_APPROVED", "Buyer has not approved the order at PayPal yet.")
            return _result(txn, "NOT_APPROVED",
                           "The buyer has not approved this order at PayPal yet.",
                           _retry_url(txn)), False
        if exc.issue == "INSTRUMENT_DECLINED":
            metrics.paypal_captures_total.labels("declined_retry").inc()
            audit("CAPTURE_INSTRUMENT_DECLINED",
                  "PayPal declined the funding source; buyer can choose another.")
            return _result(txn, "DECLINED_RETRY",
                           "PayPal declined the payment method. Return to PayPal to "
                           "choose another one.", _retry_url(txn)), False
        if exc.issue == "ORDER_ALREADY_CAPTURED":
            # A previous attempt captured it; confirm the real state, read-only.
            try:
                resp = await paypal.get_order(order_id)
            except Exception:  # noqa: BLE001
                logger.warning("paypal_get_order_failed txn_id=%s", txn.id, exc_info=True)
                audit("CAPTURE_STATE_UNKNOWN", "Order already captured; confirmation lookup failed.")
                return _result(txn, "RETRYABLE_ERROR",
                               "Could not confirm the capture with PayPal yet; retry shortly."), False
        elif exc.retryable:
            metrics.paypal_captures_total.labels("error").inc()
            audit("CAPTURE_RETRYABLE_ERROR", f"PayPal returned HTTP {exc.status_code}; state unchanged.")
            return _result(txn, "RETRYABLE_ERROR",
                           "PayPal capture failed; it is safe to retry."), False
        else:
            metrics.paypal_captures_total.labels("failed").inc()
            _safe_transition(db, txn, target="FAILED", actor=lifecycle.PAYPAL,
                             event_type="CAPTURE_FAILED",
                             reason=f"PayPal rejected the capture ({exc.issue or exc.status_code}).")
            return _result(txn, "FAILED", "PayPal rejected the capture."), False
    except httpx.TransportError:
        logger.warning("paypal_capture_transport_error txn_id=%s", txn.id, exc_info=True)
        metrics.paypal_captures_total.labels("error").inc()
        audit("CAPTURE_RETRYABLE_ERROR", "Network error talking to PayPal; state unchanged.")
        return _result(txn, "RETRYABLE_ERROR", "PayPal capture failed; it is safe to retry."), False
    except Exception:  # noqa: BLE001
        metrics.paypal_captures_total.labels("error").inc()
        _safe_transition(db, txn, target="FAILED", actor=lifecycle.PAYPAL,
                         event_type="CAPTURE_FAILED", reason="PayPal capture failed.")
        # Full detail server-side only; the client gets a generic message.
        logger.warning("paypal_capture_failed txn_id=%s", txn.id, exc_info=True)
        return _result(txn, "FAILED", "PayPal capture failed."), True

    verdict, cap, reason = _verify_capture(txn, resp)
    cap_id = (cap or {}).get("id")
    meta = {"capture_id": cap_id, "capture_status": (cap or {}).get("status"),
            "order_status": resp.get("status")}

    if verdict == "COMPLETED":
        metrics.paypal_captures_total.labels("success").inc()
        txn.paypal_capture_id = cap_id
        _safe_transition(db, txn, target="COMPLETED", actor=lifecycle.PAYPAL,
                         event_type="CAPTURE_VERIFIED", reason=reason, metadata=meta)
        return _result(txn, "COMPLETED", "Payment captured and verified with PayPal."), False
    if verdict == "PENDING":
        metrics.paypal_captures_total.labels("pending").inc()
        if cap_id:
            txn.paypal_capture_id = cap_id  # lets the webhook match it later
            db.commit()
        audit("CAPTURE_PENDING", reason + " Awaiting PayPal's completion webhook.", meta)
        return _result(txn, "PENDING",
                       "PayPal is still processing this capture. It will complete "
                       "automatically when PayPal confirms it."), False
    if verdict in ("FAILED", "MISMATCH"):
        metrics.paypal_captures_total.labels("failed").inc()
        if cap_id:
            txn.paypal_capture_id = cap_id
        _safe_transition(db, txn, target="FAILED", actor=lifecycle.PAYPAL,
                         event_type="CAPTURE_DECLINED" if verdict == "FAILED"
                         else "CAPTURE_VERIFICATION_FAILED",
                         reason=reason, metadata=meta)
        return _result(txn, "FAILED", reason), False
    # UNKNOWN
    metrics.paypal_captures_total.labels("error").inc()
    audit("CAPTURE_STATE_UNKNOWN", reason + " State unchanged.", meta)
    return _result(txn, "RETRYABLE_ERROR",
                   "PayPal's response could not be confirmed; retry shortly."), False


@router.post(
    "/transactions/execute",
    response_model=TransactionView,
    dependencies=[Depends(require_api_key)],
)
async def execute_transaction(req: ExecuteRequest, db: Session = Depends(get_db)):
    """Create the PayPal Sandbox order for a human-APPROVED transaction.

    Idempotent: if an order already exists, the existing checkout link is
    returned instead of creating a second order.
    """
    txn = db.get(Transaction, req.transaction_id)
    if txn is None:
        raise HTTPException(404, "Transaction not found.")

    async with _lock_for(txn.id):
        db.refresh(txn)
        if txn.status == "PAYPAL_CREATED" and txn.paypal_order_id and txn.approve_url:
            lifecycle.record_event(
                db, transaction_id=txn.id, correlation_id=txn.correlation_id,
                event_type="EXECUTE_DUPLICATE", actor=lifecycle.SYSTEM,
                reason="PayPal order already exists; returned the existing checkout link.",
                metadata={"order_id": txn.paypal_order_id},
            )
            return TransactionView.model_validate(txn)
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
                request_id=_idem_key(txn, "order"),
            )
        except (paypal.PayPalNotConfigured, paypal.PayPalNotSandbox) as exc:
            raise HTTPException(503, str(exc))
        except Exception as exc:  # noqa: BLE001
            metrics.paypal_orders_total.labels("error").inc()
            logger.warning("paypal_order_failed txn_id=%s", txn.id, exc_info=True)
            if _is_retryable(exc):
                # Outcome unknown/transient: keep APPROVED. The stable request id
                # means a retry can't create a duplicate order.
                lifecycle.record_event(
                    db, transaction_id=txn.id, correlation_id=txn.correlation_id,
                    event_type="ORDER_RETRYABLE_ERROR", actor=lifecycle.PAYPAL,
                    reason="PayPal order creation failed transiently; state unchanged.",
                )
                raise HTTPException(502, "PayPal order creation failed; it is safe to retry.")
            lifecycle.transition(db, txn, target="FAILED", actor=lifecycle.PAYPAL,
                                 event_type="ORDER_FAILED", reason="PayPal order creation failed.")
            # Generic message so internal/PayPal exception text never reaches the client.
            raise HTTPException(502, "PayPal order creation failed.")

        approve_url = paypal.extract_approve_url(order)
        if not order.get("id") or not paypal.is_sandbox_approve_url(approve_url):
            metrics.paypal_orders_total.labels("error").inc()
            lifecycle.transition(db, txn, target="FAILED", actor=lifecycle.PAYPAL,
                                 event_type="ORDER_FAILED",
                                 reason="PayPal response had no Sandbox checkout link.")
            raise HTTPException(502, "PayPal order creation failed.")

        metrics.paypal_orders_total.labels("success").inc()
        txn.paypal_order_id = order["id"]
        txn.approve_url = approve_url
        lifecycle.transition(
            db, txn, target="PAYPAL_CREATED", actor=lifecycle.PAYPAL,
            event_type="ORDER_CREATED", reason="PayPal Sandbox order created.",
            metadata={"order_id": order["id"]},
        )
        return TransactionView.model_validate(txn)


@router.post(
    "/transactions/capture",
    response_model=TransactionView,
    dependencies=[Depends(require_api_key)],
)
async def capture_transaction(req: CaptureRequest, db: Session = Depends(get_db)):
    """Manual capture fallback (same verified logic as the PayPal return)."""
    txn = db.get(Transaction, req.transaction_id)
    if txn is None:
        raise HTTPException(404, "Transaction not found.")
    if not txn.paypal_order_id:
        raise HTTPException(400, "No PayPal order to capture.")
    if txn.status not in ("PAYPAL_CREATED", "COMPLETED"):
        raise HTTPException(400, f"Cannot capture a transaction in {txn.status}.")

    async with _lock_for(txn.id):
        result, internal_error = await _capture_verified(db, txn)

    if internal_error:
        raise HTTPException(502, "PayPal capture failed.")
    if result.outcome in ("NOT_APPROVED", "DECLINED_RETRY"):
        raise HTTPException(409, result.message)
    if result.outcome == "RETRYABLE_ERROR":
        raise HTTPException(502, result.message)
    return result.transaction


def _txn_for_token(db: Session, token: str) -> Transaction:
    txn = db.query(Transaction).filter(Transaction.paypal_order_id == token).first()
    if txn is None:
        raise HTTPException(404, "No PayGuard transaction matches this PayPal order.")
    return txn


@router.post(
    "/paypal/return",
    response_model=CheckoutResult,
    dependencies=[Depends(require_api_key)],
)
async def paypal_return(req: PayPalReturnRequest, db: Session = Depends(get_db)):
    """PayPal redirected the buyer back: capture and verify the real result.

    Buyer approval at PayPal is recorded separately from PayGuard's human
    approval. Safe to call repeatedly (page refresh): an already-completed
    transaction is reported, never captured twice.
    """
    txn = _txn_for_token(db, req.token)
    async with _lock_for(txn.id):
        db.refresh(txn)
        if txn.status == "PAYPAL_CREATED":
            lifecycle.record_event(
                db, transaction_id=txn.id, correlation_id=txn.correlation_id,
                event_type="PAYPAL_BUYER_RETURNED", actor=lifecycle.PAYPAL,
                reason="Buyer returned from PayPal Sandbox checkout. This is PayPal "
                       "checkout approval, separate from PayGuard human approval; "
                       "the capture below verifies it.",
                metadata={"order_id": txn.paypal_order_id, "payer_id": req.payer_id},
            )
        result, _ = await _capture_verified(db, txn)
        return result


@router.post(
    "/paypal/cancel",
    response_model=CheckoutResult,
    dependencies=[Depends(require_api_key)],
)
def paypal_cancel(req: PayPalReturnRequest, db: Session = Depends(get_db)):
    """Buyer cancelled at PayPal. Record it; keep PAYPAL_CREATED so they can retry."""
    txn = _txn_for_token(db, req.token)
    if txn.status != "PAYPAL_CREATED":
        return _result(txn, "NO_CHANGE", f"Transaction is {txn.status}; nothing changed.")
    lifecycle.record_event(
        db, transaction_id=txn.id, correlation_id=txn.correlation_id,
        event_type="PAYPAL_CHECKOUT_CANCELLED", actor=lifecycle.USER,
        reason="Buyer cancelled PayPal Sandbox checkout. No money moved; checkout can be retried.",
        metadata={"order_id": txn.paypal_order_id},
    )
    db.refresh(txn)
    return _result(txn, "CANCELLED",
                   "Checkout was cancelled at PayPal. No money moved. You can retry checkout.",
                   _retry_url(txn))


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
