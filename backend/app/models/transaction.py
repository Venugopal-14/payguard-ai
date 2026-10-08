from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Transaction(Base):
    """A payment proposal and its lifecycle through the PayGuard control plane."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    mission_id: Mapped[int | None] = mapped_column(
        ForeignKey("missions.id"), nullable=True
    )

    # What is being paid for.
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    merchant: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)

    # PayGuard decision layer.
    # decision: APPROVE | NEEDS_APPROVAL | BLOCK
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    risk_score: Mapped[float] = mapped_column(Numeric(5, 2), default=0, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), default="LOW", nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # lifecycle status: PROPOSED | AWAITING_APPROVAL | BLOCKED | APPROVED |
    #                   PAYPAL_CREATED | COMPLETED | FAILED
    status: Mapped[str] = mapped_column(String(40), nullable=False)

    # PayPal linkage.
    paypal_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    paypal_capture_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approve_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
