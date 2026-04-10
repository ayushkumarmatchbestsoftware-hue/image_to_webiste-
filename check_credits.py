import asyncio
from core.db import get_engine
from sqlalchemy import text

async def check_database_credits():
    print("\n" + "="*50)
    print("🔍 CHECKING DATABASE FOR RECENT CREDIT TRANSACTIONS")
    print("="*50 + "\n")
    
    eng = get_engine()
    
    try:
        async with eng.connect() as conn:
            # Check user balance
            print("--- 1. CURRENT USER BALANCES ---")
            balances = await conn.execute(text("SELECT \"userId\", balance, version FROM user_credit_balances LIMIT 5"))
            rows = balances.fetchall()
            if not rows:
                print("No users found in user_credit_balances table.")
            for row in rows:
                print(f"👤 User: {row.userId}")
                print(f"💰 Balance: {row.balance}")
                print(f"🔄 Version: {row.version}\n")

            # Check recent transactions
            print("--- 2. LATEST 5 CREDIT TRANSACTIONS ---")
            txs = await conn.execute(text(
                "SELECT \"userId\", reason, credits, direction, \"referenceId\", \"balanceAfter\", \"createdAt\" "
                "FROM credit_transactions ORDER BY \"createdAt\" DESC LIMIT 5"
            ))
            tx_rows = txs.fetchall()
            if not tx_rows:
                print("No transactions recorded yet.")
            for row in tx_rows:
                print(f"[{row.createdAt}] User: {row.userId}")
                print(f"   {row.direction.upper()}: {row.credits} credits (Reason: {row.reason})")
                print(f"   Balance After: {row.balanceAfter}")
                print(f"   Reference/Website ID: {row.referenceId}\n")
                
    except Exception as e:
        print(f"❌ Database error: {e}")

if __name__ == "__main__":
    asyncio.run(check_database_credits())
