import uuid
from sqlalchemy import (
    Column,
    String,
    Text,
    TIMESTAMP,
    CheckConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from core.db import Base


class WebsiteInfo(Base):
    __tablename__ = "website_info"

    __table_args__ = (
        CheckConstraint(
            "char_length(prompt) <= 1000",
            name="check_prompt_length_max_1000"
        ),
    )

    # Primary Key
    website_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    user_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    prompt = Column(
        Text,
        nullable=False,
    )

    status = Column(
        String(20),
        nullable=False,
        default="ready",
    )

    final_url = Column(
        Text,
        nullable=True,
    )

    progress = Column(
        String(50),
        nullable=True,
    )

    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# -------------------------------
# WORD LIMIT VALIDATION FUNCTION
# -------------------------------

def validate_prompt(prompt: str) -> str:
    words = prompt.split()

    if len(words) > 1000:
        raise ValueError("Prompt cannot exceed 1000 words")

    return prompt