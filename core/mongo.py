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

async def get_website_layout(website_id: str) -> list:
    """
    Fetches the layout array for a given website_id.
    """
    print(f"[MONGO IO] -> get_website_layout | website_id={website_id}")
    collection = get_websites_collection()
    doc = await collection.find_one({"website_id": str(website_id)}, {"layout": 1})
    layout = doc.get("layout", []) if doc else []
    print(f"[MONGO IO] <- get_website_layout | SUCCESS | layout_size={len(layout)}")
    return layout

async def update_website_layout(website_id: str, new_layout: list):
    """
    Updates the layout array for a given website_id.
    Matches either by the 'website_id' field or by the internal '_id'.
    """
    from bson import ObjectId
    print(f"[MONGO IO] -> update_website_layout | website_id={website_id} | sections={len(new_layout)}")
    collection = get_websites_collection()
    query = {"$or": [{"website_id": str(website_id)}]}
    try:
        query["$or"].append({"_id": ObjectId(website_id)})
    except:
        pass
        
    result = await collection.update_one(query, {"$set": {"layout": new_layout}})
    print(f"[MONGO IO] <- update_website_layout | matched={result.matched_count} | modified={result.modified_count}")

async def insert_chat_message(website_id: str, role: str, content: str):
    """
    Pushes a chat message into the chat_messages array of the website document.
    """
    from bson import ObjectId
    print(f"[MONGO IO] -> insert_chat_message | website_id={website_id} | role={role} | content_len={len(content)}")
    collection = get_websites_collection()
    query = {"$or": [{"website_id": str(website_id)}]}
    try:
        query["$or"].append({"_id": ObjectId(website_id)})
    except:
        pass
        
    result = await collection.update_one(query, {"$push": {"chat_messages": {"role": role, "content": content}}})
    print(f"[MONGO IO] <- insert_chat_message | matched={result.matched_count} | modified={result.modified_count}")

async def update_website_final_url(website_id: str, final_url: str):
    """
    Updates the final_url field for a given website_id.
    """
    from bson import ObjectId
    print(f"[MONGO IO] -> update_website_final_url | website_id={website_id} | target_url={final_url}")
    collection = get_websites_collection()
    
    query = {"$or": [{"website_id": str(website_id)}]}
    try:
        query["$or"].append({"_id": ObjectId(website_id)})
    except:
        pass
        
    # Extra check for existing owner (Diagnostic)
    record = await collection.find_one(query)
    if record:
        print(f"[MONGO IO]    Verified Owner: {record.get('user_id')}")
    else:
        print(f"[MONGO IO]    WARNING: Could not find record for ID {website_id}")

    result = await collection.update_one(query, {"$set": {"final_url": final_url}})
    print(f"[MONGO IO] <- update_website_final_url | matched={result.matched_count} | modified={result.modified_count}")
    return result.modified_count > 0
