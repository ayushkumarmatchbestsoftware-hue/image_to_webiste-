import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "")
MONGO_DB_NAME = os.getenv("MONGO_DB", "xelta_db")

# Reuse the client globally
_mongo_client = None

def get_mongo_client() -> AsyncIOMotorClient:
    global _mongo_client
    if _mongo_client is None:
        if not MONGODB_URL:
            # Fallback for unexpected situations
            raise ValueError("MONGODB_URL is missing from environment variables.")
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
    We embed images_data into the website_data document for easier retrieval.
    """
    collection = get_websites_collection()
    website_data["images"] = images_data
    await collection.insert_one(website_data)

async def get_website_layout(website_id: str) -> list:
    """
    Fetches the layout array for a given website_id.
    """
    collection = get_websites_collection()
    doc = await collection.find_one({"website_id": str(website_id)}, {"layout": 1})
    if doc and "layout" in doc:
        return doc["layout"]
    return []

async def update_website_layout(website_id: str, new_layout: list):
    """
    Updates the layout array for a given website_id.
    """
    collection = get_websites_collection()
    await collection.update_one(
        {"website_id": str(website_id)},
        {"$set": {"layout": new_layout}}
    )

async def insert_chat_message(website_id: str, role: str, content: str):
    """
    Pushes a chat message into the chat_messages array of the website document.
    """
    collection = get_websites_collection()
    await collection.update_one(
        {"website_id": str(website_id)},
        {"$push": {"chat_messages": {"role": role, "content": content}}}
    )
