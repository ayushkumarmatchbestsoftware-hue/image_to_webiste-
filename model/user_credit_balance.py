import uuid
from sqlalchemy import (
    Column,
    String,
    Integer,
    TIMESTAMP,
)
from sqlalchemy.sql import func
from core.db import Base

class UserCreditBalance(Base):
    __tablename__ = "user_credit_balances"

    user_id = Column(
        "userId",
        String(36),
        primary_key=True,
        nullable=False,
    )

    balance = Column(
        Integer,
        nullable=False,
        default=0,
    )

    version = Column(
        Integer,
        nullable=False,
        default=0,
    )

    created_at = Column(
        "createdAt",
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at = Column(
        "updatedAt",
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __mapper_args__ = {
        "version_id_col": version,
    }