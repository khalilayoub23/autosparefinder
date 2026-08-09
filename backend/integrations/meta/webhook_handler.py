"""
integrations/meta/webhook_handler.py — Meta webhook signature verification and event parsing.

Meta sends all webhook events to our registered callback URL with an
X-Hub-Signature-256 header. This module verifies the signature (HMAC-SHA256)
and parses the event payload into a structured dict.

Registration: `FACEBOOK_APP_SECRET` must be set in .env.

Usage (in routes/webhooks.py or similar):
    from integrations.meta.webhook_handler import verify_signature, parse_event

    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_signature(raw_body, signature):
        raise HTTPException(status_code=403, detail="invalid webhook signature")

    event = parse_event(raw_body)
    # dispatch on event["object"] → "page" | "instagram" | "whatsapp_business_account"
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

log = logging.getLogger("integrations.meta.webhook_handler")

# Supported webhook object types
PAGE_OBJECT = "page"
INSTAGRAM_OBJECT = "instagram"
WHATSAPP_OBJECT = "whatsapp_business_account"


def verify_signature(raw_body: bytes, signature_header: str) -> bool:
    """Verify Meta's HMAC-SHA256 webhook signature.

    Args:
      raw_body:         Raw request body bytes (before any decoding)
      signature_header: Value of X-Hub-Signature-256 header
                        (format: "sha256=<hexdigest>")

    Returns True if the signature is valid, False otherwise.
    Fails CLOSED: returns False if FACEBOOK_APP_SECRET is not set.
    """
    app_secret = os.getenv("FACEBOOK_APP_SECRET", "").strip()
    if not app_secret:
        log.error("webhook_handler.verify_signature: FACEBOOK_APP_SECRET not set — failing closed")
        return False

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_hex = signature_header.removeprefix("sha256=")
    digest = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected_hex)


def parse_event(raw_body: bytes) -> dict[str, Any]:
    """Parse a Meta webhook payload into a structured event dict.

    Returns a unified event dict:
    {
        "object":  "page" | "instagram" | "whatsapp_business_account",
        "entries": [{ "id", "time", "changes": [...] | "messaging": [...] }],
        "raw":     {…original JSON…}
    }
    """
    try:
        payload = json.loads(raw_body)
    except Exception as exc:
        log.warning("webhook_handler.parse_event: invalid JSON: %s", exc)
        return {"object": "unknown", "entries": [], "raw": {}}

    obj = payload.get("object", "")
    entries = payload.get("entry", [])
    parsed_entries = []

    for entry in entries:
        e: dict = {"id": entry.get("id"), "time": entry.get("time")}

        if obj == PAGE_OBJECT:
            # Page webhooks carry "changes" (comments, reactions, page events)
            e["changes"] = entry.get("changes", [])
            # Also carries "messaging" for Messenger (if configured)
            e["messaging"] = entry.get("messaging", [])

        elif obj == INSTAGRAM_OBJECT:
            # IG webhooks carry "changes" with field=comments/mentions/story_insights
            e["changes"] = entry.get("changes", [])

        elif obj == WHATSAPP_OBJECT:
            # WhatsApp webhooks carry "changes" with field=messages/statuses
            e["changes"] = entry.get("changes", [])

        parsed_entries.append(e)

    log.debug("webhook_handler.parse_event: object=%s entries=%d", obj, len(parsed_entries))
    return {"object": obj, "entries": parsed_entries, "raw": payload}


def extract_page_comments(event: dict) -> list[dict]:
    """Extract comment events from a parsed PAGE webhook event.

    Returns list of: {comment_id, post_id, from_id, from_name, message, created_time}
    """
    comments = []
    for entry in event.get("entries", []):
        for change in entry.get("changes", []):
            if change.get("field") != "feed":
                continue
            v = change.get("value", {})
            if v.get("item") != "comment":
                continue
            comments.append({
                "comment_id": v.get("comment_id"),
                "post_id": v.get("post_id"),
                "from_id": v.get("from", {}).get("id"),
                "from_name": v.get("from", {}).get("name"),
                "message": v.get("message", ""),
                "created_time": v.get("created_time"),
            })
    return comments


def extract_ig_comments(event: dict) -> list[dict]:
    """Extract comment events from a parsed INSTAGRAM webhook event.

    Returns list of: {comment_id, media_id, from_id, text, timestamp}
    """
    comments = []
    for entry in event.get("entries", []):
        for change in entry.get("changes", []):
            if change.get("field") != "comments":
                continue
            v = change.get("value", {})
            comments.append({
                "comment_id": v.get("id"),
                "media_id": v.get("media", {}).get("id"),
                "from_id": v.get("from", {}).get("id"),
                "text": v.get("text", ""),
                "timestamp": v.get("timestamp"),
            })
    return comments
