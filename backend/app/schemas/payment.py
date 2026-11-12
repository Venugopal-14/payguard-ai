from datetime import datetime

from pydantic import BaseModel, Field, computed_field


class MissionRequest(BaseModel):
    """A user hands the agent a mission with a spending policy."""

    instruction: str = Field(min_length=3, max_length=1000)
    budget: float = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    approval_threshold: float = Field(default=200, ge=0)


class RiskFactorView(BaseModel):
    factor: str
    weight: float
    description: str


class RiskView(BaseModel):
    score: float
    level: str
    signals: list[str]
    factors: list[RiskFactorView] = []
    explanation: str = ""
    recommended_action: str = "APPROVE"


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

    # `decision` is the policy RECOMMENDATION (APPROVE | NEEDS_APPROVAL | BLOCK)
    # and never changes. `human_approval` is the separate, derived human-approval
    # state: PENDING | APPROVED | NOT_APPLICABLE. Read-only; nothing is stored.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def human_approval(self) -> str:
        from app.engine.lifecycle import human_approval_state

        return human_approval_state(self.status)

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


class PayPalReturnRequest(BaseModel):
    """Query params PayPal appends to the return/cancel URL (token = order id)."""

    token: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9]+$")
    payer_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9]+$")


class CheckoutResult(BaseModel):
    """Outcome of a capture attempt / checkout return, verified against PayPal.

    outcome: COMPLETED | ALREADY_COMPLETED | PENDING | NOT_APPROVED |
             DECLINED_RETRY | RETRYABLE_ERROR | FAILED | CANCELLED | NOT_CAPTURABLE
    """

    outcome: str
    message: str
    transaction: TransactionView
    # Sandbox checkout URL to send the buyer back to (retry / cancelled cases).
    retry_url: str | None = None


class AuditEventView(BaseModel):
    id: int
    transaction_id: int | None
    correlation_id: str | None
    event_type: str
    actor: str
    previous_state: str | None
    new_state: str | None
    reason: str
    created_at: datetime

    class Config:
        from_attributes = True
