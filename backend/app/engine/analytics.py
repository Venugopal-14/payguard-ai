"""Fraud-prevention analytics computed from persisted transactions.

Honesty rules baked in:
  * Numbers are labeled SIMULATED. Nothing here claims real-world savings.
  * COMPLETED payments (real spend) are excluded from all "blocked"/"saved"
    figures.
  * A BLOCK is only counted as a *simulated fraud attempt* when its decision is
    BLOCK, status is BLOCKED, and its stored reasoning contains a scam-type
    signal. Ordinary budget-control blocks are counted separately.
  * Each transaction row is counted once (no double counting).
  * Everything is derived from the database, never Prometheus counters.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.transaction import Transaction

# The risk engine stores factor *descriptions* (not keys) in `reasoning`.
# Only the strongest scam-type signals qualify a block as *simulated fraud*.
# An unknown merchant is a risk signal but NOT, by itself, evidence of fraud,
# so it is deliberately excluded here (kept as an ordinary risk factor).
#   blocked_merchant   -> "is explicitly blocked"
#   blocked_category   -> "is explicitly blocked"
#   suspicious_terms   -> "Suspicious terms detected"
SCAM_SIGNATURES = (
    "is explicitly blocked",
    "suspicious terms detected",
)

# Statuses that represent a real, settled spend -> excluded from "saved".
SPEND_STATUSES = {"COMPLETED"}

APPROVED_STATUSES = {"APPROVED", "PAYPAL_CREATED", "COMPLETED"}


def _is_scam_reasoning(reasoning: object) -> bool:
    if not isinstance(reasoning, str):
        return False
    low = reasoning.lower()
    return any(sig in low for sig in SCAM_SIGNATURES)


def compute_fraud_analytics(db: Session) -> dict:
    txns = db.query(Transaction).all()

    total = len(txns)

    # Distributions.
    risk_distribution = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
    decision_breakdown = {"APPROVE": 0, "NEEDS_APPROVAL": 0, "BLOCK": 0}

    pending = 0
    approved = 0
    decided = 0

    simulated_fraud_attempts = 0
    # Amounts are grouped by currency so different currencies are never summed
    # together as if equal. Keys are currency codes, values are Decimal totals.
    simulated_blocked_by_ccy: dict[str, Decimal] = {}
    policy_blocked_by_ccy: dict[str, Decimal] = {}

    def _add_amount(bucket: dict[str, Decimal], ccy: str, amount: Decimal) -> None:
        bucket[ccy] = bucket.get(ccy, Decimal("0")) + amount

    for t in txns:
        level = (t.risk_level or "").upper()
        if level in risk_distribution:
            risk_distribution[level] += 1

        decision = (t.decision or "").upper()
        if decision in decision_breakdown:
            decision_breakdown[decision] += 1

        status = (t.status or "").upper()

        # Approval-rate accounting: pending items are not yet decided.
        if status == "AWAITING_APPROVAL":
            pending += 1
        else:
            # Only count rows with a recognized decision as "decided"; a missing
            # or unknown decision must NOT be silently treated as approved.
            if decision in decision_breakdown:
                decided += 1
                # Count as approved only when BOTH the decision is APPROVE and
                # the status is approved-like. Prevents an inconsistent record
                # (e.g. decision BLOCK but status APPROVED) from inflating the
                # approval rate.
                if decision == "APPROVE" and status in APPROVED_STATUSES:
                    approved += 1

        # Blocked-amount accounting (exclude any real spend).
        if status == "BLOCKED" and status not in SPEND_STATUSES:
            ccy = (t.currency or "USD").upper()
            amount = Decimal(str(t.amount or 0))
            if _is_scam_reasoning(t.reasoning):
                simulated_fraud_attempts += 1
                _add_amount(simulated_blocked_by_ccy, ccy, amount)
            else:
                _add_amount(policy_blocked_by_ccy, ccy, amount)

    if decided > 0:
        approval_rate = round(approved / decided * 100, 1)
    else:
        approval_rate = None  # UI shows "—"

    return {
        "label": "SIMULATED — not real-world savings",
        "total_transactions": total,
        "pending_approval": pending,
        "decided_transactions": decided,
        "approved_transactions": approved,
        "approval_rate_pct": approval_rate,
        "simulated_fraud_attempts_blocked": simulated_fraud_attempts,
        # Per-currency maps so the UI never adds different currencies together.
        "simulated_blocked_amount_by_currency": {
            k: float(v) for k, v in simulated_blocked_by_ccy.items()
        },
        "policy_blocked_amount_by_currency": {
            k: float(v) for k, v in policy_blocked_by_ccy.items()
        },
        "risk_distribution": risk_distribution,
        "decision_breakdown": decision_breakdown,
    }
