import uuid
from sqlalchemy import (
    Column,
    String,
    Text,
    Integer,
    Boolean,
    Float,
    TIMESTAMP,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from core.db import Base


class ImageInfo(Base):
    __tablename__ = "image_info"

    # Primary Key
    image_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    # Link to website
    website_id = Column(
        UUID(as_uuid=True),
        ForeignKey("website_info.website_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # # User reference
    # user_id = Column(
    #     UUID(as_uuid=True),
    #     nullable=False,
    #     index=True,
    # )

    # File details
    file_url = Column(
        Text,
        nullable=False,
    )

    file_name = Column(
        String(255),
        nullable=True,
    )

    file_format = Column(
        String(10),
        nullable=True,
    )

    file_size_mb = Column(
        Float,
        nullable=True,
    )

    # Image dimensions
    width = Column(
        Integer,
        nullable=True,
    )

    height = Column(
        Integer,
        nullable=True,
    )

    # Controls placement (IMPORTANT)
    image_type = Column(
        String(50),  # logo / hero / portfolio / background
        nullable=False,
        default="portfolio",
    )

    # AI generated or user uploaded
    is_generated = Column(
        Boolean,
        default=False,
    )

    # Timestamp
    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )