from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
import os
from sqlalchemy.orm import DeclarativeBase
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


# ----------------------------
# Base Class (Models will inherit from this)
# ----------------------------
class Base(DeclarativeBase):
    pass

# (Model imports moved inside init_db to avoid circular issues)


# ----------------------------
# Create Async Engine
# ----------------------------
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
)


# ----------------------------
# Session Factory
# ----------------------------
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ----------------------------
# Dependency for FastAPI
# ----------------------------
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# ----------------------------
# Initialize Database (Dev Only)
# ----------------------------
async def init_db():
    # Import inside function to register models only when needed
    from model.website_schema import WebsiteInfo
    from model.img_info_schema import ImageInfo
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)