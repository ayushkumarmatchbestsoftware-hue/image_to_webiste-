import uuid
from sqlalchemy import (
    Column,
    String,
    Integer,
    TIMESTAMP,
    JSON,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID, ENUM
from sqlalchemy.sql import func

from core.db import Base


class CreditTransaction(Base):
    __tablename__ = "credit_transactions"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    user_id = Column(
        "userId",
        String(36),
        nullable=False,
    )

    subscription_id = Column(
        "subscriptionId",
        UUID(as_uuid=True),
        nullable=True,
    )

    reason = Column(
        String(30),
        nullable=False,
    )

    credits = Column(
        Integer,
        nullable=False,
    )

    direction = Column(
        ENUM("credit", "debit", create_type=False, name="enum_credit_transactions_direction"),
        nullable=False,
        default="credit",
    )

    reference_id = Column(
        "referenceId",
        String(255),
        nullable=False,
        unique=True,
    )

    source = Column(
        String(30),
        nullable=False,
        default="system",
    )

    balance_after = Column(
        "balanceAfter",
        Integer,
        nullable=True,
    )

    tx_metadata = Column(
        "metadata",
        JSON,
        nullable=True,
    )

    created_at = Column(
        "createdAt",
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("idx_credit_tx_user_id", "userId"),
        Index("idx_credit_tx_subscription_id", "subscriptionId"),
    )