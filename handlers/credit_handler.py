import logging
from typing import Dict, Any

from services.credit_debit_service import website_credit_debit_service

logger = logging.getLogger(__name__)


async def website_credits_debits(payload: Dict[str, Any]) -> Dict[str, Any]:
    
    result = await website_credit_debit_service(payload)

    if result.get("success"):
        return result

    return {
        "success": False,
        "error": result.get("error", "INTERNAL_ERROR"),
    }