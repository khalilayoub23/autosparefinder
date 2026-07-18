"""
Script: routes/thumbnails.py
Purpose: Serve part thumbnails from the private Contabo Object Storage bucket through our own
         domain. The bucket is private (Contabo won't serve anonymous public GETs); this route
         streams the object with a long immutable Cache-Control so Cloudflare edge-caches it —
         the backend fetches each thumbnail from S3 at most once. We serve ONLY the image bytes,
         so no supplier link/ad can ride along (part image + optional part-name caption only).

Endpoint: GET /api/v1/thumbnails/{key:path}   (public; keys are opaque `parts/ab/<uuid>.jpg`)

Author: AutoSpareFinder Agent
Last Updated: 2026-07-18
"""
from fastapi import APIRouter, HTTPException, Response

import s3_storage

router = APIRouter()

_ALLOWED_CT = {"image/jpeg", "image/png", "image/webp"}


# Negative cache: let Cloudflare/edge cache 404s so a flood of random/invalid keys can't
# repeatedly hit the backend + S3 (the un-rate-limited thumbnails location's one DoS vector).
_NEG_CACHE = {"Cache-Control": "public, max-age=600"}


@router.get("/api/v1/thumbnails/{key:path}", tags=["Thumbnails"])
async def get_thumbnail(key: str):
    # Only ever serve from the thumbnail prefixes; reject traversal/absolute/arbitrary keys.
    # thumbs/ = content-addressed (deduped) objects; parts/ = legacy part-id-addressed.
    if (not key or ".." in key or key.startswith("/") or "\\" in key
            or "%2e" in key.lower() or not key.startswith(("thumbs/", "parts/"))):
        return Response(status_code=404, headers=_NEG_CACHE)
    if not s3_storage.s3_enabled():
        raise HTTPException(status_code=503, detail="Thumbnail storage not configured")
    obj = s3_storage.get_object(key)
    if obj is None:
        return Response(status_code=404, headers=_NEG_CACHE)
    data, content_type = obj
    if content_type not in _ALLOWED_CT:
        content_type = "image/jpeg"
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
