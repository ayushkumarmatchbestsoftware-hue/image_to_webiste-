import logging
from typing import Dict, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_session_factory
from model.user_credit_balance import UserCreditBalance

logger = logging.getLogger(__name__)

async def get_user_real_credit(user_id: str) -> Dict[str, Any]:

    if not user_id:
        return {
            "ok": False,
            "error": "INVALID_USER_ID",
            "data": {"balance": 0},
        }

    # -------------------------------------------------
    # Fallback directly to Database (Redis removed for compatibility)
    # -------------------------------------------------
    try:
        factory = get_session_factory()
        async with factory() as session:  # type: AsyncSession
            stmt = select(UserCreditBalance).where(
                UserCreditBalance.user_id == str(user_id)
            )
            result = await session.execute(stmt)
            balance_row = result.scalar_one_or_none()

            balance = balance_row.balance if balance_row else 0

    except Exception:
        logger.exception("Database credit lookup failed")
        return {
            "ok": False,
            "error": "INTERNAL_ERROR",
            "data": {"balance": 0},
        }

    return {
        "ok": True,
        "data": {"balance": balance},
        "source": "db",
    }