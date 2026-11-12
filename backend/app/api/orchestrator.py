"""Multi-agent mission orchestrator endpoint (dry run, no payments)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.orchestrator.orchestrator import (
    OrchestrateRequest,
    OrchestrationResult,
    run_orchestration,
)

router = APIRouter(prefix="/api", tags=["orchestrator"])


@router.post("/orchestrate", response_model=OrchestrationResult)
async def orchestrate(req: OrchestrateRequest, db: Session = Depends(get_db)):
    """Run the specialist-agent workflow for a mission.

    Read-only: stores nothing and never touches PayPal. Execution still needs
    the existing human approval flow.
    """
    return await run_orchestration(db, req)
