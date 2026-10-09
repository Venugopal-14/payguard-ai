"""Deterministic policy engine.

Takes a proposed purchase + the mission's policy + a risk result and returns a
final decision: APPROVE, NEEDS_APPROVAL, or BLOCK. This is the gate that stands
between the AI agent and PayPal. The AI can never bypass it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.engine.risk import RiskResult

APPROVE = "APPROVE"
NEEDS_APPROVAL = "NEEDS_APPROVAL"
BLOCK = "BLOCK"


@dataclass
class PolicyDecision:
    decision: str
    reasons: list[str]


def decide(
    *,
    amount: float,
    budget: float,
    approval_threshold: float,
    risk: RiskResult,
) -> PolicyDecision:
    reasons: list[str] = []

    # Hard blocks first.
    if amount <= 0:
        return PolicyDecision(BLOCK, ["Amount must be positive."])

    if amount > budget:
        reasons.append(
            f"Amount {amount:.2f} exceeds the mission budget {budget:.2f}."
        )
        return PolicyDecision(BLOCK, reasons)

    if risk.level in ("HIGH", "CRITICAL"):
        reasons.append(
            f"Risk level is {risk.level} (score {risk.score}); "
            "blocking automatically."
        )
        return PolicyDecision(BLOCK, reasons)

    # Needs human approval.
    if amount >= approval_threshold:
        reasons.append(
            f"Amount {amount:.2f} meets or exceeds the approval threshold "
            f"{approval_threshold:.2f}; human approval required."
        )
        return PolicyDecision(NEEDS_APPROVAL, reasons)

    if risk.level == "MEDIUM":
        reasons.append(
            f"Risk level is MEDIUM (score {risk.score}); human approval required."
        )
        return PolicyDecision(NEEDS_APPROVAL, reasons)

    # Auto-approve.
    reasons.append(
        f"Within budget, below approval threshold, and risk is LOW "
        f"(score {risk.score}). Auto-approved."
    )
    return PolicyDecision(APPROVE, reasons)
