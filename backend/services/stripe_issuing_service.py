import stripe
import logging
import os
import httpx
from decimal import Decimal

logger = logging.getLogger(__name__)

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

PROFIT_MARGIN = Decimal("1.45")

async def get_usd_rate() -> Decimal:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get("https://api.frankfurter.app/latest?from=USD&to=ILS")
            data = response.json()
            rate = Decimal(str(data["rates"]["ILS"]))
            logger.info(f"USD/ILS rate: {rate}")
            return rate
    except Exception as e:
        logger.error(f"Failed to fetch exchange rate: {e}")
        return Decimal("3.7")

def calculate_supplier_amount(customer_paid_ils: Decimal) -> Decimal:
    return (customer_paid_ils / PROFIT_MARGIN).quantize(Decimal("0.01"))

async def ils_to_usd(amount_ils: Decimal) -> Decimal:
    rate = await get_usd_rate()
    return (amount_ils / rate).quantize(Decimal("0.01"))

async def ensure_issuing_balance(required_usd: Decimal) -> bool:
    try:
        balance = stripe.Balance.retrieve()
        issuing_available = balance.issuing.available
        current_usd_cents = next(
            (b["amount"] for b in issuing_available if b["currency"] == "usd"), 0
        )
        required_cents = int(required_usd * 100)
        buffer_cents = 1000

        if current_usd_cents < required_cents + buffer_cents:
            topup_cents = required_cents + buffer_cents - current_usd_cents + 500
            stripe.Topup.create(
                amount=topup_cents,
                currency="usd",
                description="Auto top-up for supplier order"
            )
            logger.info(f"Topped up Issuing balance by ${topup_cents/100:.2f}")
        return True
    except stripe.error.InvalidRequestError as e:
        if "Top-ups are limited" in str(e):
            logger.error(f"Stripe top-up weekly limit reached: {e}")
        else:
            logger.error(f"Stripe InvalidRequestError in top-up: {e}")
        return False
    except Exception as e:
        logger.error(f"ensure_issuing_balance failed: {e}", exc_info=True)
        return False

async def get_or_create_supplier_card(supplier_name: str, supplier_id: str) -> dict | None:
    try:
        cards = stripe.issuing.Card.list(type="virtual", status="active", limit=100)
        for card in cards.auto_paging_iter():
            meta = card.metadata or {}
            if meta.get("supplier_id") == supplier_id:
                logger.info(f"Reusing existing card {card.id} for supplier {supplier_name}")
                return {
                    "card_id": card.id,
                    "last4": card.last4,
                    "exp_month": card.exp_month,
                    "exp_year": card.exp_year,
                }

        cardholders = stripe.issuing.Cardholder.list(limit=100)
        cardholder_id = None
        for ch in cardholders.auto_paging_iter():
            if ch.metadata.get("autosparefinder") == "supplier_payments":
                cardholder_id = ch.id
                break

        if not cardholder_id:
            cardholder = stripe.issuing.Cardholder.create(
                name="AutoSpareFinder Supplier Payments",
                email=os.getenv("SUPPLIER_PAYMENTS_EMAIL", "payments@autosparefinder.co.il"),
                type="company",
                billing={
                    "address": {
                        "line1": "1 HaMasger St",
                        "city": "Tel Aviv",
                        "country": "IL",
                        "postal_code": "6721407",
                    }
                },
                metadata={"autosparefinder": "supplier_payments"}
            )
            cardholder_id = cardholder.id
            logger.info(f"Created new cardholder: {cardholder_id}")

        card = stripe.issuing.Card.create(
            cardholder=cardholder_id,
            currency="usd",
            type="virtual",
            status="active",
            metadata={
                "supplier_id": supplier_id,
                "supplier_name": supplier_name,
            }
        )
        logger.info(f"Created new virtual card {card.id} for supplier {supplier_name}")
        return {
            "card_id": card.id,
            "last4": card.last4,
            "exp_month": card.exp_month,
            "exp_year": card.exp_year,
        }

    except Exception as e:
        logger.error(f"get_or_create_supplier_card failed for {supplier_name}: {e}")
        return None

async def update_card_spending_limit(card_id: str, amount_usd: Decimal) -> bool:
    try:
        amount_cents = int(amount_usd * 100)
        stripe.issuing.Card.modify(
            card_id,
            spending_controls={
                "spending_limits": [
                    {"amount": amount_cents, "interval": "per_authorization"}
                ]
            }
        )
        logger.info(f"Updated spending limit on card {card_id} to ${amount_usd}")
        return True
    except Exception as e:
        logger.error(f"update_card_spending_limit failed for card {card_id}: {e}")
        return False

async def prepare_supplier_payment(
    order_id: str,
    customer_paid_ils: Decimal,
    supplier_name: str,
    supplier_id: str,
) -> dict | None:
    try:
        supplier_ils = calculate_supplier_amount(customer_paid_ils)
        supplier_usd = await ils_to_usd(supplier_ils)
        logger.info(f"Order {order_id}: customer={customer_paid_ils}ILS supplier={supplier_ils}ILS ({supplier_usd}USD)")

        balance_ok = await ensure_issuing_balance(supplier_usd)
        if not balance_ok:
            logger.error(f"Order {order_id}: failed to ensure Issuing balance")
            return None

        card = await get_or_create_supplier_card(supplier_name, supplier_id)
        if not card:
            logger.error(f"Order {order_id}: failed to get/create card")
            return None

        limit_ok = await update_card_spending_limit(card["card_id"], supplier_usd)
        if not limit_ok:
            logger.error(f"Order {order_id}: failed to update spending limit")
            return None

        return {
            "card": card,
            "supplier_amount_ils": float(supplier_ils),
            "supplier_amount_usd": float(supplier_usd),
        }

    except Exception as e:
        logger.error(f"prepare_supplier_payment failed for order {order_id}: {e}")
        return None
