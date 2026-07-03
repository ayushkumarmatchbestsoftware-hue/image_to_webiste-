import asyncio
import logging
import re
from typing import Dict, Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_session_factory
from model.user_credit_balance import UserCreditBalance
from model.credit_transaction import CreditTransaction

logger = logging.getLogger(__name__)

CREDIT_ERROR = {
    "INVALID_PARAMS": "INVALID_PARAMS",
    "INSUFFICIENT_CREDITS": "INSUFFICIENT_CREDITS",
    "INTERNAL_ERROR": "INTERNAL_ERROR",
}


async def website_credit_debit_service(
    input_data: Dict[str, Any]
) -> Dict[str, Any]:

    logger.debug("credit debit input: %s", input_data)

    # Normalize IDs (VERY IMPORTANT)
    user_id = str(input_data.get("userId"))
    resource_type = input_data.get("resourceType")
    resource_id = str(input_data.get("resourceId"))
    description = input_data.get("description", "")

    match = (
        re.search(r"task ([a-z0-9-]+)", description, re.I)
        if description else None
    )

    job_id = (
        input_data.get("jobId")
        or (match.group(1) if match else f"usage_{resource_id}")
    )

    # -------------------------------
    # Validate usage cost
    # -------------------------------
    if input_data.get("type") == "USAGE":
        raw_amount = input_data.get("amount")
        if not isinstance(raw_amount, (int, float)) or raw_amount >= 0:
            return {"success": False, "error": CREDIT_ERROR["INVALID_PARAMS"]}
        cost = abs(int(raw_amount))
    else:
        cost = input_data.get("cost")

    if not all([user_id, job_id, resource_type, resource_id, cost]) or cost <= 0:
        return {"success": False, "error": CREDIT_ERROR["INVALID_PARAMS"]}

    reference_id = (
        input_data.get("reference_id")
        or f"{job_id}:{resource_type}:{resource_id}"
    )

    # -------------------------------
    # Retry loop (DB safety)
    # -------------------------------
    factory = get_session_factory()
    for attempt in range(3):

        async with factory() as db:
            try:
                async with db.begin():

                    # -------------------------------
                    # Idempotency check
                    # -------------------------------
                    existing_tx_result = await db.execute(
                        select(CreditTransaction).where(
                            CreditTransaction.reference_id == reference_id,
                            CreditTransaction.reason == "usage",
                        )
                    )
                    existing = existing_tx_result.scalar_one_or_none()

                    if existing:

                        return {
                            "success": True,
                            "userId": user_id,
                            "remainingCredits": existing.balance_after,
                            "duplicate": True,
                        }

                    # -------------------------------
                    # Lock balance row
                    # -------------------------------
                    balance_result = await db.execute(
                        select(UserCreditBalance)
                        .where(UserCreditBalance.user_id == user_id)
                        .with_for_update()
                    )
                    balance = balance_result.scalar_one_or_none()

                    if not balance:
                        balance = UserCreditBalance(
                            user_id=user_id,
                            balance=0,
                            version=0,
                        )
                        db.add(balance)
                        await db.flush()

                    # -------------------------------
                    # Insufficient credits
                    # -------------------------------
                    if balance.balance < cost:
                        return {
                            "success": False,
                            "error": CREDIT_ERROR["INSUFFICIENT_CREDITS"],
                        }

                    # -------------------------------
                    # Deduct credits
                    # -------------------------------
                    new_balance = balance.balance - cost
                    balance.balance = new_balance
                    balance.version += 1


                    tx = CreditTransaction(
                        user_id=user_id,
                        reason="usage",
                        credits=cost,
                        direction="debit",
                        reference_id=reference_id,
                        source="Website Builder",
                        balance_after=new_balance,
                        tx_metadata={
                            "jobId": job_id,
                            "resourceType": resource_type,
                            "resourceId": resource_id,
                            "attempt": attempt,
                        },
                    )

                    db.add(tx)

                # Transaction committed here automatically

                return {
                    "success": True,
                    "userId": user_id,
                    "remainingCredits": new_balance,
                }

            except SQLAlchemyError:
                logger.exception(
                    "Credit debit failed (attempt %s)", attempt
                )
                await asyncio.sleep(0.1 * (attempt + 1))

    return {"success": False, "error": CREDIT_ERROR["INTERNAL_ERROR"]}