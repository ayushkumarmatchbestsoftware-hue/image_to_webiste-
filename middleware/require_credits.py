from functools import wraps
from flask import request, jsonify, g
import os

from services.credit_query_service import get_user_real_credit
from dotenv import load_dotenv
load_dotenv()

ENABLE_CREDIT_SYSTEM = os.getenv("ENABLE_CREDIT_SYSTEM", "True") == "True"

def require_credits(amount: int):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):

            # -------------------------------
            # Credit system toggle
            # -------------------------------
            if not ENABLE_CREDIT_SYSTEM:
                return await func(*args, **kwargs)

            # -------------------------------
            # Extract user_id from Flask g
            # -------------------------------
            user_id = getattr(g, "user_id", None)
            if not user_id:
                return jsonify({"error": "User not authenticated"}), 401

            # -------------------------------
            # Fetch real credit balance
            # -------------------------------
            result = await get_user_real_credit(user_id)

            balance = (
                result["data"]["balance"]
                if result.get("ok")
                else 0
            )

            if balance < amount:
                print(f">>> [CREDIT BLOCK] User {user_id} rejected. Has {balance} credits, needs {amount}.", flush=True)
                return jsonify({
                    "error": "INSUFFICIENT_CREDITS",
                    "required": amount,
                    "available": balance,
                }), 402

            # -------------------------------
            # Continue request
            # -------------------------------
            return await func(*args, **kwargs)

        return wrapper
    return decorator