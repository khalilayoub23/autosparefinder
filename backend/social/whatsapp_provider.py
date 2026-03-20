"""WhatsApp provider abstraction — send/receive via pluggable backends.

Current implementation: TwilioWhatsAppProvider (uses Twilio Messaging API over
raw httpx — NOT the blocking Twilio SDK).
"""
import os
import base64
import hashlib
import hmac
from abc import ABC, abstractmethod

import httpx


class WhatsAppProvider(ABC):
    """Abstract base — swap Twilio for Meta Cloud API or 360dialog by subclassing."""

    @abstractmethod
    async def send_message(self, to: str, body: str) -> dict:
        """Send a text message. Returns {"ok": bool, "sid": str|None, "error": str|None}."""
        ...

    @abstractmethod
    async def parse_incoming(self, data: dict) -> dict:
        """Extract normalised fields from a raw webhook payload.
        Returns {"from": str, "body": str, "profile_name": str}.
        """
        ...


class TwilioWhatsAppProvider(WhatsAppProvider):
    """Twilio WhatsApp Messaging API over httpx (fully async — no blocking SDK)."""

    def __init__(self):
        self._sid   = os.getenv("TWILIO_ACCOUNT_SID", "")
        self._token = os.getenv("TWILIO_AUTH_TOKEN", "")
        self._from  = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
        self._url   = f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"

    async def send_message(self, to: str, body: str) -> dict:
        if not self._sid or not self._token:
            return {"ok": False, "sid": None, "error": "Twilio credentials not configured"}

        # Ensure the destination is prefixed correctly
        if not to.startswith("whatsapp:"):
            to = f"whatsapp:{to}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    self._url,
                    data={"From": self._from, "To": to, "Body": body},
                    auth=(self._sid, self._token),
                )
            data = resp.json()
        except httpx.HTTPError as exc:
            return {"ok": False, "sid": None, "error": str(exc)}

        if resp.status_code in (200, 201):
            return {"ok": True, "sid": data.get("sid"), "error": None}
        return {"ok": False, "sid": None, "error": data.get("message", f"HTTP {resp.status_code}")}

    async def parse_incoming(self, data: dict) -> dict:
        """Parse Twilio webhook form fields (already decoded from application/x-www-form-urlencoded)."""
        return {
            "from":         data.get("From", ""),
            "body":         data.get("Body", ""),
            "profile_name": data.get("ProfileName", ""),
        }

    def validate_signature(self, auth_token: str, url: str, params: dict, signature: str) -> bool:
        """Validate X-Twilio-Signature to confirm the request is from Twilio.
        https://www.twilio.com/docs/usage/webhooks/webhooks-security#validating-signatures-from-twilio
        """
        s = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
        computed = base64.b64encode(
            hmac.new(auth_token.encode(), s.encode(), hashlib.sha1).digest()
        ).decode()
        return hmac.compare_digest(computed, signature)


def get_whatsapp_provider() -> WhatsAppProvider:
    return TwilioWhatsAppProvider()
