import os
# NOTE: motor/pymongo (~7MB RSS just to import) is intentionally NOT imported
# at module level — client construction here was already lazy (see
# get_mongo_client below); this just defers the underlying import to match,
# so nothing pulls in motor until a Mongo operation actually happens.
from dotenv import load_dotenv

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "")
MONGO_DB_NAME = os.getenv("MONGO_DB", "xelta_db")

# Reuse the client globally
_mongo_client = None

def get_mongo_client():
    global _mongo_client
    if _mongo_client is None:
        if not MONGODB_URL:
            # Fallback for unexpected situations
            raise ValueError("MONGODB_URL is missing from environment variables.")
        from motor.motor_asyncio import AsyncIOMotorClient
        _mongo_client = AsyncIOMotorClient(MONGODB_URL)
    return _mongo_client

def get_mongo_db():
    """Returns the main database instance."""
    client = get_mongo_client()
    return client[MONGO_DB_NAME]

def get_websites_collection():
    """Returns the website-generator collection."""
    db = get_mongo_db()
    return db["website-generator"]

async def insert_website_data(website_data: dict, images_data: list):
    """
    Inserts website data and its associated images into the website-generator collection.
    """
    print(f"[MONGO IO] -> insert_website_data | website_id={website_data.get('website_id')} | site_name='{website_data.get('site_name')}'")
    collection = get_websites_collection()
    website_data["images"] = images_data
    result = await collection.insert_one(website_data)
    print(f"[MONGO IO] <- insert_website_data | SUCCESS | doc_id={result.inserted_id}")
