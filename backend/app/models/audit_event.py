from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class AuditEvent(Base):
    """An immutable record of a single lifecycle transition.

    Answers: who/what changed this transaction's state, when, why, and from
    what to what. Never stores secrets. `correlation_id` ties together the
    whole mission -> transaction -> PayPal -> webhook -> reconciliation chain.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    transaction_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("transactions.id"), index=True, nullable=True
    )
    correlation_id: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True
    )

    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    actor: Mapped[str] = mapped_column(String(40), nullable=False)

    previous_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    new_state: Mapped[str | None] = mapped_column(String(40), nullable=True)

    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    event_metadata: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
