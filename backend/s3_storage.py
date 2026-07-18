"""
s3_storage.py — Contabo Object Storage (S3-compatible) client for part thumbnails.

Config (env, secret only in .env): S3_ENDPOINT, S3_REGION, S3_ACCESS_KEY, S3_SECRET_KEY,
S3_BUCKET, THUMB_PUBLIC_BASE.

The bucket is kept PRIVATE (Contabo does not serve anonymous public GETs even with a
public-read policy — verified 2026-07-18: 401). Thumbnails are served through our own backend
(`routes/thumbnails.py` → `/thumbnails/{key}`) which streams the object with a long immutable
cache header so Cloudflare edge-caches it. This also guarantees we control exactly what's
served (no supplier links/ads — part image + optional part-name caption only).

Author: AutoSpareFinder Agent
Last Updated: 2026-07-18
"""
import os
import threading
from typing import Optional, Tuple

_client = None
_lock = threading.Lock()

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "").rstrip("/")
S3_REGION = os.getenv("S3_REGION", "eu2")
S3_BUCKET = os.getenv("S3_BUCKET", "part-thumbnails")
THUMB_PUBLIC_BASE = os.getenv("THUMB_PUBLIC_BASE", "https://autosparefinder.co.il/thumbnails").rstrip("/")


def s3_enabled() -> bool:
    return bool(S3_ENDPOINT and os.getenv("S3_ACCESS_KEY") and os.getenv("S3_SECRET_KEY") and S3_BUCKET)


def get_client():
    """Cached boto3 S3 client (thread-safe, single-flight)."""
    global _client
    if _client is not None:
        return _client
    with _lock:
        if _client is None:
            import boto3
            from botocore.config import Config
            _client = boto3.client(
                "s3",
                endpoint_url=S3_ENDPOINT,
                aws_access_key_id=os.getenv("S3_ACCESS_KEY"),
                aws_secret_access_key=os.getenv("S3_SECRET_KEY"),
                region_name=S3_REGION,
                config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
            )
    return _client


def thumb_key(part_id: str) -> str:
    """Sharded object key for a part thumbnail (2-char prefix keeps listings fast)."""
    pid = str(part_id).replace("/", "")
    return f"parts/{pid[:2]}/{pid}.jpg"


def thumb_url(part_id: str) -> str:
    """The public-facing (our-domain) URL a client uses to fetch the thumbnail."""
    return f"{THUMB_PUBLIC_BASE}/{thumb_key(part_id)}"


def upload_bytes(key: str, data: bytes, content_type: str = "image/jpeg") -> bool:
    try:
        get_client().put_object(
            Bucket=S3_BUCKET, Key=key, Body=data, ContentType=content_type,
            CacheControl="public, max-age=31536000, immutable",
        )
        return True
    except Exception as exc:
        print(f"[s3] upload {key} failed: {exc}")
        return False


def object_exists(key: str) -> bool:
    try:
        get_client().head_object(Bucket=S3_BUCKET, Key=key)
        return True
    except Exception:
        return False


def get_object(key: str) -> Optional[Tuple[bytes, str]]:
    """Return (bytes, content_type) for a key, or None if missing/error. Used by the proxy."""
    try:
        r = get_client().get_object(Bucket=S3_BUCKET, Key=key)
        return r["Body"].read(), r.get("ContentType", "image/jpeg")
    except Exception:
        return None
