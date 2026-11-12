"""Tests for the multi-agent mission orchestrator (dry run, deterministic)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.agent import Proposal
from app.core.config import settings
from app.db.database import Base, get_db
from app.models.transaction import Transaction
from app.orchestrator import orchestrator as orch
from app.orchestrator.orchestrator import OrchestrateRequest, run_orchestration
from app.api.orchestrator import router as orchestrator_router


@pytest.fixture()
def Session(monkeypatch):
    # Force the deterministic fallback proposal path (no LLM, no network).
    monkeypatch.setattr(settings, "ollama_base_url", "", raising=False)
    monkeypatch.setattr(settings, "ollama_model", "", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _run(Session, **kw):
    db = Session()
    try:
        return asyncio.run(run_orchestration(db, OrchestrateRequest(**kw)))
    finally:
        db.close()


def _agents(result):
    return [s.agent for s in result.steps]


def test_normal_mission_approves_with_full_timeline(Session):
    r = _run(Session, instruction="Buy a keyboard under $50 from Amazon")
    assert _agents(r) == ["Planner", "Merchant", "Risk", "Policy", "Supervisor"]
    assert r.final_decision == "APPROVE"
    assert all(s.status != "skipped" for s in r.steps)
    assert r.steps[0].output["constraints"]["max_budget"] == 50
    # Every non-final step names its handoff.
    assert all(s.handoff for s in r.steps)


def test_always_requires_human_approval_and_never_executes(Session):
    r = _run(Session, instruction="Buy a keyboard under $50 from Amazon")
    assert r.requires_human_approval is True
    assert r.payment_executed is False
    assert r.execution_proposal is not None
    assert "human must approve" in r.execution_proposal["next_step"]


def test_above_threshold_needs_approval(Session):
    r = _run(Session, instruction="Buy a keyboard under $50 from Amazon",
             approval_threshold=20)
    assert r.final_decision == "NEEDS_APPROVAL"
    assert r.requires_human_approval is True


def test_high_risk_mission_is_blocked(Session):
    r = _run(Session, instruction="Buy a gift card with bitcoin", budget=100)
    risk = next(s for s in r.steps if s.agent == "Risk")
    assert risk.output["level"] in ("HIGH", "CRITICAL")
    assert r.final_decision == "BLOCK"
    assert r.execution_proposal is None
    assert "No PayPal order can be created" in r.explanation


def test_blocked_merchant_is_blocked(Session, monkeypatch):
    async def _fake(*, instruction, budget, currency="USD"):
        return Proposal(
            description="Mystery item", merchant="silkroad", amount=30.0,
            currency=currency, agent_notes="x", source="fallback",
        )

    monkeypatch.setattr(orch, "propose_purchase", _fake)
    r = _run(Session, instruction="Buy something", budget=100)
    merchant = next(s for s in r.steps if s.agent == "Merchant")
    assert merchant.status == "blocked"
    assert merchant.output["merchant_classification"] == "blocked"
    assert r.final_decision == "BLOCK"
    assert r.payment_executed is False


def test_missing_budget_halts_with_needs_info(Session):
    r = _run(Session, instruction="Buy a keyboard")
    assert r.final_decision == "NEEDS_INFO"
    assert r.steps[0].status == "incomplete"
    assert [s.status for s in r.steps[1:4]] == ["skipped"] * 3
    assert r.execution_proposal is None
    assert r.payment_executed is False


def test_labels_deterministic_when_no_llm(Session):
    r = _run(Session, instruction="Buy a keyboard under $50")
    assert r.mode == "deterministic-orchestration"
    assert "no LLM" in r.mode_label
    merchant = next(s for s in r.steps if s.agent == "Merchant")
    assert merchant.output["proposal_source"] == "fallback"
    assert "no external verification" in merchant.output["verification"]


def test_api_route_is_dry_run_and_stores_nothing(Session):
    app = FastAPI()
    app.include_router(orchestrator_router)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    client = TestClient(app)

    resp = client.post("/api/orchestrate",
                       json={"instruction": "Buy a keyboard under $50 from Amazon"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["payment_executed"] is False
    assert len(body["steps"]) == 5

    db = Session()
    assert db.query(Transaction).count() == 0  # nothing persisted
    db.close()

    bad = client.post("/api/orchestrate", json={"instruction": "x"})
    assert bad.status_code == 422  # validation, not a crash


# --------------------------------------------------------------------------
# Regression: budget extraction (never silently use a wrong budget)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Buy a keyboard under $50 from a trusted merchant", 50),
        ("Buy a keyboard under 50", 50),
        ("Get a mouse below $75.5", 75.5),
        ("Order a cable up to $20", 20),
    ],
)
def test_budget_extracted_from_mission_text(Session, text, expected):
    r = _run(Session, instruction=text)
    c = r.steps[0].output["constraints"]
    assert c["max_budget"] == expected
    assert c["budget_from_mission_text"] == expected
    assert c["budget_manual_override"] is None
    assert c["budget_source"] == "mission text"
    assert r.steps[0].status == "ok"


def test_proposed_amount_follows_extracted_budget_not_a_default(Session):
    r = _run(Session, instruction="Buy a keyboard under $50 from a trusted merchant")
    merchant = next(s for s in r.steps if s.agent == "Merchant")
    # Fallback targets 95% of the $50 cap = 47.50; certainly not $1.
    assert merchant.output["amount"] == 47.5


def test_manual_override_conflict_is_explicit_not_silent(Session):
    r = _run(Session, instruction="Buy a keyboard under $50", budget=1)
    planner = r.steps[0]
    c = planner.output["constraints"]
    assert c["max_budget"] == 1  # lowest applies
    assert c["budget_conflict"] is True
    assert c["budget_from_mission_text"] == 50
    assert c["budget_manual_override"] == 1
    assert planner.status == "flagged"
    assert "50.00" in planner.summary and "1.00" in planner.summary


def test_higher_manual_budget_cannot_exceed_mission_cap(Session):
    r = _run(Session, instruction="Buy a keyboard under $50", budget=500)
    c = r.steps[0].output["constraints"]
    assert c["max_budget"] == 50
    assert c["budget_conflict"] is True


def test_matching_manual_budget_is_not_a_conflict(Session):
    r = _run(Session, instruction="Buy a keyboard under $50", budget=50)
    c = r.steps[0].output["constraints"]
    assert c["max_budget"] == 50
    assert c["budget_conflict"] is False


# --------------------------------------------------------------------------
# Regression: policy recommendation vs. human authorization wording
# --------------------------------------------------------------------------

def test_policy_approve_is_not_labelled_auto_approved(Session):
    r = _run(Session, instruction="Buy a keyboard under $50 from Amazon")
    assert r.final_decision == "APPROVE"
    blob = r.model_dump_json().lower()
    assert "auto-approved" not in blob
    policy_step = next(s for s in r.steps if s.agent == "Policy")
    assert "not payment authorization" in policy_step.summary


def test_supervisor_separates_policy_from_authorization(Session):
    r = _run(Session, instruction="Buy a keyboard under $50 from Amazon")
    assert r.execution_status == "NOT_EXECUTED"
    assert r.human_authorization == "REQUIRED"
    assert r.status_line == (
        "Policy: APPROVE | Payment execution: NOT EXECUTED | "
        "Human authorization: REQUIRED"
    )
    sup = r.steps[-1]
    assert sup.output["policy_recommendation"] == "APPROVE"
    assert sup.output["human_authorization"] == "REQUIRED"
    assert sup.output["payment_executed"] is False


def test_needs_approval_status_line(Session):
    r = _run(Session, instruction="Buy a keyboard under $50 from Amazon",
             approval_threshold=20)
    assert r.status_line.startswith("Policy: NEEDS_APPROVAL")
    assert r.human_authorization == "REQUIRED"


def test_blocked_has_no_human_authorization_path(Session):
    r = _run(Session, instruction="Buy a gift card with bitcoin", budget=100)
    assert r.final_decision == "BLOCK"
    assert r.human_authorization == "NOT_APPLICABLE"
    assert r.requires_human_approval is False
    assert r.execution_status == "NOT_EXECUTED"
    assert "NOT APPLICABLE" in r.status_line


def test_missing_budget_status_line(Session):
    r = _run(Session, instruction="Buy a keyboard")
    assert r.human_authorization == "NOT_APPLICABLE"
    assert r.execution_status == "NOT_EXECUTED"
    assert "NEEDS_INFO" in r.status_line
