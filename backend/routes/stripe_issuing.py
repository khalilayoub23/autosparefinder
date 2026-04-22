import json
import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhooks/stripe/issuing/authorize")
async def authorize_issuing(request: Request):
    payload = await request.body()

    try:
        data = json.loads(payload)
    except Exception:
        logger.error("Invalid payload received")
        return JSONResponse(status_code=400, content={"error": "Invalid payload"})

    if data.get("type") == "issuing_authorization.request":
        auth = data["data"]["object"]
        auth_id = auth["id"]
        amount = auth.get("pending_request", {}).get("amount", 0)
        currency = auth.get("pending_request", {}).get("currency", "usd")
        merchant = auth.get("merchant_data", {}).get("name", "Unknown")
        card_id = auth.get("card", {}).get("id", "")

        logger.info(
            f"Issuing auth request: {auth_id} | card={card_id} | "
            f"merchant={merchant} | amount={amount/100:.2f} {currency.upper()}"
        )

        # New Stripe API: approve by returning approved=true in response
        return JSONResponse(
            status_code=200,
            content={"approved": True}
        )

    return JSONResponse(status_code=200, content={"status": "ignored"})
