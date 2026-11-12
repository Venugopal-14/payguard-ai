"""Multi-agent payment mission orchestrator (deterministic, rule-based).

Five specialist agents hand a purchase mission to each other:

  Planner -> Merchant -> Risk -> Policy -> Supervisor

HONESTY NOTES
  * The specialist agents here are deterministic code, NOT independent LLM
    agents. The only step that can use an LLM is the Merchant agent's call to
    the existing ``propose_purchase`` (which itself falls back to rules when no
    LLM is configured). The result reports which one actually ran.
  * Risk and Policy agents REUSE the existing engines (``evaluate_risk`` and
    ``policy.decide``); nothing is duplicated or bypassed.
  * This is a DRY RUN. It never stores a transaction, never calls PayPal, and
    never approves or executes a payment. ``requires_human_approval`` is always
    True and ``payment_executed`` is always False.
  * No external merchant verification is performed; the Merchant agent only
    classifies against PayGuard's internal known/blocked merchant lists.
"""

from __future__ import annotations

import re
import time
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.agent import propose_purchase
from app.engine import policy
from app.engine.risk import (
    BLOCKED_MERCHANTS,
    KNOWN_MERCHANTS,
    HistoryRecord,
    evaluate_risk,
)
from app.models.transaction import Transaction

MODE = "deterministic-orchestration"

_CAP_RE = re.compile(
    r"(?:under|below|less than|max(?:imum)?|up to|within)\s*\$?\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


class OrchestrateRequest(BaseModel):
    instruction: str = Field(min_length=3, max_length=1000)
    budget: float | None = Field(default=None, gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    approval_threshold: float = Field(default=200, ge=0)


class AgentStep(BaseModel):
    agent: str
    role: str
    status: str  # ok | flagged | blocked | incomplete | skipped
    summary: str
    input: dict[str, Any]
    output: dict[str, Any]
    handoff: str
    duration_ms: float


class OrchestrationResult(BaseModel):
    mode: str
    mode_label: str
    # Policy RECOMMENDATION only (APPROVE | NEEDS_APPROVAL | BLOCK | NEEDS_INFO).
    # It is never payment authorization.
    final_decision: str
    # Execution and authorization are reported separately from the policy result.
    execution_status: str  # always NOT_EXECUTED
    human_authorization: str  # REQUIRED | NOT_APPLICABLE (blocked / missing data)
    status_line: str
    requires_human_approval: bool
    payment_executed: bool
    explanation: str
    execution_proposal: dict[str, Any] | None
    steps: list[AgentStep]


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def _skipped(agent: str, role: str, why: str) -> AgentStep:
    return AgentStep(
        agent=agent, role=role, status="skipped", summary=why,
        input={}, output={}, handoff="—", duration_ms=0.0,
    )


async def run_orchestration(
    db: Session, req: OrchestrateRequest
) -> OrchestrationResult:
    steps: list[AgentStep] = []
    currency = req.currency.upper()

    # ------------------------------------------------------------------ Planner
    t = time.perf_counter()
    cap_match = _CAP_RE.search(req.instruction)
    parsed_cap = float(cap_match.group(1)) if cap_match else None
    prefer_trusted = "trusted" in req.instruction.lower()

    candidates = [v for v in (req.budget, parsed_cap) if v is not None]
    effective_budget = min(candidates) if candidates else None
    budget_conflict = (
        req.budget is not None
        and parsed_cap is not None
        and abs(req.budget - parsed_cap) > 1e-9
    )

    plan_steps = [
        "Propose one concrete purchase that fits the mission",
        "Classify the merchant against known/blocked lists",
        "Score transaction risk with the deterministic risk engine",
        "Apply spending policy (approve / needs approval / block)",
        "Supervisor summarises and hands off to a human approver",
    ]
    constraints = {
        "max_budget": effective_budget,
        # Both inputs are reported so nothing is applied silently.
        "budget_from_mission_text": parsed_cap,
        "budget_manual_override": req.budget,
        "budget_conflict": budget_conflict,
        "currency": currency,
        "approval_threshold": req.approval_threshold,
        "prefer_trusted_merchant": prefer_trusted,
        "budget_source": (
            "manual override and mission text (lowest applies)"
            if req.budget is not None and parsed_cap is not None
            else "manual override" if req.budget is not None
            else "mission text" if parsed_cap is not None
            else "missing"
        ),
    }
    if budget_conflict:
        budget_note = (
            f"Mission text says {parsed_cap:.2f} but manual budget is "
            f"{req.budget:.2f}; the lower value ({effective_budget:.2f}) applies."
        )
    elif effective_budget is not None:
        budget_note = (
            f"Budget {effective_budget:.2f} {currency} taken from "
            f"{constraints['budget_source']}."
        )
    else:
        budget_note = ""

    if effective_budget is None:
        steps.append(AgentStep(
            agent="Planner", role="Turns the mission into constraints and steps",
            status="incomplete",
            summary="No budget found. Provide a budget (or say 'under $N' in the mission).",
            input={"instruction": req.instruction},
            output={"constraints": constraints, "missing": ["budget"]},
            handoff="Halted — Supervisor requests the missing data.",
            duration_ms=_ms(t),
        ))
        steps.append(_skipped("Merchant", "Evaluates merchant and purchase context",
                              "Skipped: Planner could not establish a budget."))
        steps.append(_skipped("Risk", "Scores transaction risk (existing engine)",
                              "Skipped: no proposal to score."))
        steps.append(_skipped("Policy", "Applies spending policy (existing engine)",
                              "Skipped: no risk result."))
        steps.append(AgentStep(
            agent="Supervisor", role="Combines results and requests human approval",
            status="incomplete",
            summary="Cannot proceed without a budget. No proposal created.",
            input={"planner_status": "incomplete"},
            output={"final_decision": "NEEDS_INFO"},
            handoff="Back to the user for more information.",
            duration_ms=0.0,
        ))
        return OrchestrationResult(
            mode=MODE,
            mode_label="Rule-based orchestration (deterministic agents)",
            final_decision="NEEDS_INFO",
            execution_status="NOT_EXECUTED",
            human_authorization="NOT_APPLICABLE",
            status_line="Policy: NEEDS_INFO | Payment execution: NOT EXECUTED | "
                        "Human authorization: NOT APPLICABLE (more information needed)",
            requires_human_approval=False,
            payment_executed=False,
            explanation="The mission has no budget, so no purchase was proposed. "
                        "Add a budget and try again.",
            execution_proposal=None,
            steps=steps,
        )

    steps.append(AgentStep(
        agent="Planner", role="Turns the mission into constraints and steps",
        status="flagged" if budget_conflict else "ok",
        summary=f"{budget_note} {len(plan_steps)} steps planned.",
        input={"instruction": req.instruction, "budget": req.budget},
        output={"constraints": constraints, "plan": plan_steps},
        handoff="Constraints -> Merchant agent",
        duration_ms=_ms(t),
    ))

    # ----------------------------------------------------------------- Merchant
    t = time.perf_counter()
    proposal = await propose_purchase(
        instruction=req.instruction, budget=effective_budget, currency=currency
    )
    m_norm = proposal.merchant.strip().lower()
    if m_norm in BLOCKED_MERCHANTS:
        m_class, m_status = "blocked", "blocked"
    elif m_norm in KNOWN_MERCHANTS:
        m_class, m_status = "known", "ok"
    else:
        m_class, m_status = "unknown", "flagged"
    steps.append(AgentStep(
        agent="Merchant", role="Evaluates merchant and purchase context",
        status=m_status,
        summary=f"Proposed '{proposal.description}' from {proposal.merchant} for "
                f"{proposal.amount:.2f} {proposal.currency}; merchant is {m_class} "
                f"in PayGuard's internal lists.",
        input={"constraints": constraints},
        output={
            "description": proposal.description,
            "merchant": proposal.merchant,
            "amount": proposal.amount,
            "currency": proposal.currency,
            "category": proposal.category,
            "merchant_classification": m_class,
            "proposal_source": proposal.source,
            "proposal_provider": proposal.provider,
            "verification": "internal known/blocked lists only; "
                            "no external verification performed",
        },
        handoff="Proposal + merchant context -> Risk agent",
        duration_ms=_ms(t),
    ))

    # --------------------------------------------------------------------- Risk
    t = time.perf_counter()
    prior = db.query(Transaction).order_by(Transaction.created_at.desc()).limit(50).all()
    history = [
        HistoryRecord(merchant=x.merchant, amount=float(x.amount),
                      created_at=x.created_at, status=x.status)
        for x in prior
    ]
    risk = evaluate_risk(
        description=proposal.description, merchant=proposal.merchant,
        amount=proposal.amount, budget=effective_budget,
        currency=proposal.currency, category=proposal.category, history=history,
    )
    steps.append(AgentStep(
        agent="Risk", role="Scores transaction risk (existing risk engine)",
        status="ok" if risk.level == "LOW" else "flagged",
        summary=f"Score {risk.score:.0f}/100 -> {risk.level}. {risk.explanation}",
        input={"amount": proposal.amount, "merchant": proposal.merchant,
               "budget": effective_budget},
        output={
            "score": risk.score, "level": risk.level,
            "recommended_action": risk.recommended_action,
            "factors": [{"factor": f.factor, "weight": f.weight,
                         "description": f.description} for f in risk.factors],
        },
        handoff="Risk score + level -> Policy agent",
        duration_ms=_ms(t),
    ))

    # ------------------------------------------------------------------- Policy
    t = time.perf_counter()
    decision = policy.decide(
        amount=proposal.amount, budget=effective_budget,
        approval_threshold=req.approval_threshold, risk=risk,
    )
    p_status = {"APPROVE": "ok", "NEEDS_APPROVAL": "flagged", "BLOCK": "blocked"}[
        decision.decision
    ]
    # The engine's wording says "Auto-approved." That is a policy result, not
    # payment authorization, so we never show it that way here.
    reasons = [
        r.replace("Auto-approved.", "Meets policy criteria for approval.")
        for r in decision.reasons
    ]
    steps.append(AgentStep(
        agent="Policy", role="Applies spending policy (existing policy engine)",
        status=p_status,
        summary=f"Policy recommendation: {decision.decision}. "
                + " ".join(reasons)
                + " This is a policy result, not payment authorization.",
        input={"amount": proposal.amount, "budget": effective_budget,
               "approval_threshold": req.approval_threshold,
               "risk_level": risk.level},
        output={"decision": decision.decision, "reasons": reasons},
        handoff="Decision -> Supervisor agent",
        duration_ms=_ms(t),
    ))

    # --------------------------------------------------------------- Supervisor
    t = time.perf_counter()
    if decision.decision == "BLOCK":
        next_step = "Blocked by policy. No PayPal order can be created."
        exec_proposal = None
    else:
        next_step = (
            "Submit to the control plane. A human must approve before any "
            "PayPal order is created; this orchestrator never executes payments."
        )
        exec_proposal = {
            "description": proposal.description,
            "merchant": proposal.merchant,
            "amount": proposal.amount,
            "currency": proposal.currency,
            "policy_decision": decision.decision,
            "risk_level": risk.level,
            "next_step": next_step,
        }
    needs_human = decision.decision != "BLOCK"
    human_auth = "REQUIRED" if needs_human else "NOT_APPLICABLE"
    status_line = (
        f"Policy: {decision.decision} | Payment execution: NOT EXECUTED | "
        + ("Human authorization: REQUIRED" if needs_human
           else "Human authorization: NOT APPLICABLE (blocked)")
    )
    explanation = (
        f"{proposal.merchant} ({m_class} merchant), {proposal.amount:.2f} "
        f"{proposal.currency}, risk {risk.level} ({risk.score:.0f}/100). "
        f"{status_line}. {next_step}"
    )
    steps.append(AgentStep(
        agent="Supervisor", role="Combines results and requests human approval",
        status=p_status, summary=explanation,
        input={"merchant_status": m_status, "risk_level": risk.level,
               "policy_decision": decision.decision},
        output={"policy_recommendation": decision.decision,
                "execution_status": "NOT_EXECUTED",
                "human_authorization": human_auth,
                "payment_executed": False},
        handoff=("Human approver — no payment executed here" if needs_human
                 else "None — blocked, nothing can be executed"),
        duration_ms=_ms(t),
    ))

    llm_used = proposal.source == "llm"
    label = (
        f"Rule-based orchestration; proposal step used LLM ({proposal.provider})"
        if llm_used
        else "Rule-based orchestration (deterministic agents, fallback proposal — no LLM)"
    )
    return OrchestrationResult(
        mode=MODE, mode_label=label,
        final_decision=decision.decision,
        execution_status="NOT_EXECUTED", human_authorization=human_auth,
        status_line=status_line,
        requires_human_approval=needs_human, payment_executed=False,
        explanation=explanation, execution_proposal=exec_proposal, steps=steps,
    )
