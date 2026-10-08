from datetime import datetime

from pydantic import BaseModel, Field


class MissionRequest(BaseModel):
    """A user hands the agent a mission with a spending policy."""

    instruction: str = Field(min_length=3, max_length=1000)
    budget: float = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    approval_threshold: float = Field(default=200, ge=0)


class RiskView(BaseModel):
    score: float
    level: str
    signals: list[str]


class TransactionView(BaseModel):
    id: int
    mission_id: int | None
    description: str
    merchant: str
    amount: float
    currency: str
    decision: str
    risk_score: float
    risk_level: str
    reasoning: str
    status: str
    paypal_order_id: str | None
    paypal_capture_id: str | None
    approve_url: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class AgentView(BaseModel):
    source: str          # "llm" | "fallback"
    provider: str        # "ollama" | "openai" | "fallback"
    intent: str
    category: str
    confidence: float
    summary: str


class ProposeResponse(BaseModel):
    transaction: TransactionView
    agent_source: str
    agent: AgentView
    risk: RiskView
    policy_reasons: list[str]


class ExecuteRequest(BaseModel):
    transaction_id: int


class CaptureRequest(BaseModel):
    transaction_id: int
