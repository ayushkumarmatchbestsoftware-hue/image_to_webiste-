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
# Lazy Engine Initializer
# ----------------------------
_engine = None
_AsyncSessionLocal = None

def get_engine():
    global _engine
    if _engine is None:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool
        _engine = create_async_engine(DATABASE_URL, echo=False, poolclass=NullPool)
    return _engine

def get_session_factory():
    global _AsyncSessionLocal
    if _AsyncSessionLocal is None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
        _AsyncSessionLocal = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _AsyncSessionLocal


# ----------------------------
# Dependency for FastAPI
# ----------------------------
async def get_db():
    factory = get_session_factory()
    async with factory() as session:
        yield session


# ----------------------------
# Initialize Database (Dev Only)
# ----------------------------
async def init_db():
    # Import inside function to register models only when needed
    from model.website_schema import WebsiteInfo
    from model.img_info_schema import ImageInfo
    
    eng = get_engine()
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)