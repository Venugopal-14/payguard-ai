from datetime import datetime

from sqlalchemy import DateTime, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Mission(Base):
    """A financial mission a user hands to the agent, with its spending policy."""

    __tablename__ = "missions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # What the user wants the agent to accomplish.
    instruction: Mapped[str] = mapped_column(Text, nullable=False)

    # Policy limits.
    budget: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)

    # Payments at or above this value require explicit human approval.
    approval_threshold: Mapped[float] = mapped_column(
        Numeric(12, 2), default=200, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
