"""Boundary and gap-coverage tests (test-only; no application code changes).

These close the explicit gaps identified in the risk/policy test review:

1. Exact risk-level boundaries around the real thresholds (35 / 70 / 85),
   asserted against the actual `_level_for` function in `app.engine.risk`.
2. A MEDIUM-risk proposal within budget returning NEEDS_APPROVAL through the
   real `policy.decide` function (end-to-end on the policy side).
3. A malformed AI response path falling back deterministically and safely,
   complementing the existing `test_fallback_used_when_no_ai`.

Nothing here changes behaviour; it only pins down the contract that already
exists so regressions are caught.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agent import agent as agent_module
from app.agent.agent import Proposal, propose_purchase
from app.engine import policy
from app.engine.risk import (
    CRITICAL,
    HIGH,
    LOW,
    MEDIUM,
    RiskResult,
    _level_for,
    evaluate_risk,
)


# ---------------------------------------------------------------------------
# 1. Exact risk-level boundaries (34/35, 69/70, 84/85).
#    Thresholds come straight from risk._level_for: >=85 CRITICAL, >=70 HIGH,
#    >=35 MEDIUM, else LOW.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "score, expected",
    [
        (0, LOW),
        (34, LOW),
        (34.99, LOW),
        (35, MEDIUM),      # lower edge of MEDIUM
        (69, MEDIUM),
        (69.99, MEDIUM),
        (70, HIGH),        # lower edge of HIGH
        (84, HIGH),
        (84.99, HIGH),
        (85, CRITICAL),    # lower edge of CRITICAL
        (100, CRITICAL),
    ],
)
def test_level_boundaries(score, expected):
    assert _level_for(score) == expected


# ---------------------------------------------------------------------------
# 2. MEDIUM risk within budget -> NEEDS_APPROVAL through the real policy path.
#    We build a MEDIUM-scoring proposal from the real risk engine (unknown
#    merchant 30 + moderate budget use 10 = 40 -> MEDIUM), keep the amount
#    below the approval threshold, and assert decide() returns NEEDS_APPROVAL
#    *because of* the MEDIUM level (not the threshold rule).
# ---------------------------------------------------------------------------

def test_medium_risk_within_budget_needs_approval():
    # amount 250 of 500 budget = 50% use (moderate_budget_use, +10) and an
    # unknown merchant (+30) -> score 40 -> MEDIUM.
    risk = evaluate_risk(
        description="Office chair",
        merchant="randomshop",      # unknown merchant => +30
        amount=250,
        budget=500,                 # 50% use => +10
    )
    assert risk.level == MEDIUM, f"expected MEDIUM, got {risk.level} (score {risk.score})"

    # Keep the approval threshold ABOVE the amount so the MEDIUM rule (not the
    # threshold rule) is what forces human approval.
    decision = policy.decide(
        amount=250,
        budget=500,
        approval_threshold=400,     # 250 < 400, so threshold rule does NOT fire
        risk=risk,
    )
    assert decision.decision == policy.NEEDS_APPROVAL
    assert any("MEDIUM" in r for r in decision.reasons)


def test_medium_level_maps_to_needs_approval_directly():
    """Even a hand-built MEDIUM RiskResult routes to NEEDS_APPROVAL when the
    amount is within budget and below the approval threshold."""
    risk = RiskResult(score=50, level=MEDIUM, signals=["synthetic medium"])
    decision = policy.decide(
        amount=100, budget=1000, approval_threshold=500, risk=risk
    )
    assert decision.decision == policy.NEEDS_APPROVAL


# ---------------------------------------------------------------------------
# 3. Malformed AI response -> safe deterministic fallback.
#    We force the LLM path to be "configured" and make the LLM call raise
#    (as it would on invalid JSON / failed validation), then assert
#    propose_purchase returns a well-formed fallback proposal within budget.
# ---------------------------------------------------------------------------

def test_malformed_ai_response_falls_back_safely(monkeypatch):
    from app.core.config import settings

    # Pretend a provider is configured so propose_purchase takes the LLM path.
    monkeypatch.setattr(settings, "openai_api_key", "test-key", raising=False)
    monkeypatch.setattr(settings, "openai_base_url",
                        "https://example.invalid/v1", raising=False)
    monkeypatch.setattr(settings, "openai_model", "test-model", raising=False)
    monkeypatch.setattr(settings, "ollama_base_url", "", raising=False)
    monkeypatch.setattr(settings, "ollama_model", "", raising=False)

    assert settings.ai_configured is True

    async def _boom(*args, **kwargs):
        # Simulates malformed JSON / validation failure inside the LLM path.
        raise ValueError("LLM output failed validation: malformed JSON")

    monkeypatch.setattr(agent_module, "_propose_with_llm", _boom)

    proposal = asyncio.run(
        propose_purchase(instruction="Buy a keyboard under 80",
                         budget=100, currency="USD")
    )

    # The system must degrade gracefully to the deterministic fallback.
    assert isinstance(proposal, Proposal)
    assert proposal.source == "fallback"
    assert proposal.provider == "fallback"
    assert 0 < proposal.amount <= 100
    assert proposal.merchant  # non-empty


def test_llm_non_positive_amount_falls_back(monkeypatch):
    """If the LLM path returns a non-positive amount, propose_purchase treats
    it as a failure and falls back (per the existing guard in agent.py)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "test-key", raising=False)
    monkeypatch.setattr(settings, "ollama_base_url", "", raising=False)
    monkeypatch.setattr(settings, "ollama_model", "", raising=False)

    async def _zero_amount(instruction, budget, currency):
        return Proposal(
            description="bad", merchant="Amazon", amount=0.0,
            currency=currency, agent_notes="", source="llm", provider="openai",
        )

    monkeypatch.setattr(agent_module, "_propose_with_llm", _zero_amount)

    proposal = asyncio.run(
        propose_purchase(instruction="Buy something", budget=100, currency="USD")
    )
    assert proposal.source == "fallback"
    assert proposal.amount > 0
