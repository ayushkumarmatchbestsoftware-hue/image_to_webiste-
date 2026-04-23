import os
import uuid
import boto3
from typing import Optional
from dotenv import load_dotenv
from botocore.config import Config
load_dotenv()


# ===============================
# Environment Variables
# ===============================

R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL")

if not all([
    R2_BUCKET_NAME,
    R2_ACCOUNT_ID,
    R2_ACCESS_KEY_ID,
    R2_SECRET_ACCESS_KEY,
    R2_PUBLIC_URL,
]):
    raise RuntimeError("Missing one or more required R2 environment variables")


# ===============================
# R2 S3-Compatible Client
# ===============================

r2_client = boto3.client(
    service_name="s3",
    endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    region_name="auto",  # REQUIRED for Cloudflare R2
    config=Config(proxies={}),
)


# ===============================
# Upload Function
# ===============================

def upload_media_to_r2(
    file_bytes: bytes,
    content_type: str,
    folder: str = "websites",
    filename: Optional[str] = None,
    file_extension: Optional[str] = None,
) -> str:

    # Generate filename
    if filename:
        object_key = f"{folder}/{filename}"
    else:
        extension = file_extension or content_type.split("/")[-1]
        object_key = f"{folder}/{uuid.uuid4()}.{extension}"

    # Upload object to R2
    r2_client.put_object(
        Bucket=R2_BUCKET_NAME,
        Key=object_key,
        Body=file_bytes,
        ContentType=content_type,
    )

    # Return the full public URL so the caller can embed it in HTML templates
    return f"{R2_PUBLIC_URL}/{object_key}"

def fetch_media_from_r2(object_key: str) -> bytes:
    """
    Fetch an object from R2.
    """
    response = r2_client.get_object(
        Bucket=R2_BUCKET_NAME,
        Key=object_key,
    )
    return response["Body"].read()

def list_objects_in_folder(prefix: str):
    """
    Lists all objects in a specific R2 folder (prefix).
    """
    response = r2_client.list_objects_v2(
        Bucket=R2_BUCKET_NAME,
        Prefix=prefix
    )
    return response.get('Contents', [])
