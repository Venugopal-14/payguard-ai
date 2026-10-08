"""Structured output contract for the AI agent.

The LLM is asked to return JSON matching AgentProposal. We validate it with
Pydantic so we never trust free-form model text. If validation fails, the
caller falls back to the deterministic agent.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class AgentProposal(BaseModel):
    intent: str = Field(default="purchase")
    item: str = Field(min_length=1, max_length=200)
    merchant: str = Field(min_length=1, max_length=120)
    amount: float = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    category: str = Field(default="general", max_length=60)
    confidence: float = Field(default=0.7, ge=0, le=1)
    summary: str = Field(default="", max_length=400)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()[:3]
