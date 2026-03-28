"""
==============================================================================
AUTO SPARE - AI AGENTS (GitHub Models API)
==============================================================================
Named Agent Team:

  0. AVI   (RouterAgent)              – Smart dispatcher. Reads every message
                                        and routes to the right agent instantly.

  1. NIR   (PartsFinderAgent)         – Parts expert. Knows every OEM number,
                                        cross-reference, and fitment detail.

  2. MAYA  (SalesAgent)               – Sales pro. Presents Good/Better/Best
                                        options and closes deals in Hebrew.

  3. LIOR  (OrdersAgent)              – Logistics master. Tracks orders from
                                        placement to doorstep.

  4. TAL   (FinanceAgent)             – Finance officer. Handles payments,
                                        invoices, refunds and VAT.

  5. DANA  (ServiceAgent)             – Empathetic support. Solves post-purchase
                                        issues and complaints with care.

  6. OREN  (SecurityAgent)            – Vigilant guard. Protects accounts,
                                        manages 2FA and suspicious activity.

  7. SHIRA (MarketingAgent)           – Creative marketer. Runs campaigns,
                                        coupons, loyalty and referrals.

  8. BOAZ  (SupplierManagerAgent)     – Background supplier manager. Syncs
                                        catalogs and monitors prices silently.

  9. NOA   (SocialMediaManagerAgent)  – Social media strategist. Crafts posts,
                                        schedules content and tracks engagement.

 REX  (CatalogScraperAgent)           – The data hunter. Runs in background,
                                        scrapes real OEM+aftermarket parts from
                                        autodoc, eBay, RockAuto and more.

All agents use GitHub Models API (FREE) with GPT-4o or Claude 3.5 Sonnet.

CRITICAL BUSINESS RULES (enforced in prompts & code):
  - NEVER expose supplier name to customer - show manufacturer only
    - Price = (supplier_cost_ils × 1.45) + 18% VAT + ₪29-149 shipping (by supplier)
  - NEVER order from supplier before customer payment confirmed
  - Margin: 45% on cost
  - VAT: 18% (separate line)
    - Shipping: ₪29-149 (לפי ספק)
==============================================================================
"""

import json
import os
import random
import string
import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import logging

import httpx
from dotenv import load_dotenv
from sqlalchemy import and_, or_, select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from hf_client import hf_embed, hf_text

from BACKEND_DATABASE_MODELS import (
    AgentAction, ApprovalQueue, CatalogVersion, Conversation, Message, Notification, Order, OrderItem,
    PartsCatalog, Supplier, SupplierPart, SystemLog, SystemSetting,
    User, Vehicle, CarBrand, TruckBrand, PriceHistory, get_db, async_session_factory,
)
from BACKEND_AUTH_SECURITY import publish_notification
from resilience import retry_with_backoff

load_dotenv()

logger = logging.getLogger(__name__)

# Cap fire-and-forget asyncio.create_task() fan-out (mirrors routes/utils._TASK_SEMAPHORE).
_TASK_SEMAPHORE = asyncio.Semaphore(50)


async def _guarded_task(coro) -> None:
    """Acquire the shared semaphore before running a fire-and-forget coroutine."""
    async with _TASK_SEMAPHORE:
        await coro

# ==============================================================================
# SEARCH MISS LOGGING
# ==============================================================================

async def _log_search_miss(
    query: str,
    category: Optional[str],
    vehicle_manufacturer: Optional[str],
    user_id: Optional[str] = None,
) -> None:
    """Fire-and-forget: upsert a search_misses row for a zero-result query."""
    if not query or not query.strip():
        return
    normalized = query.lower().strip()
    try:
        async with async_session_factory() as db:
            await db.execute(
                text("""
                    INSERT INTO search_misses
                        (query, normalized_query, category, vehicle_manufacturer, user_id)
                    VALUES (:query, :norm, :cat, :vmfr, :uid)
                    ON CONFLICT (normalized_query) DO UPDATE
                        SET miss_count   = search_misses.miss_count + 1,
                            last_seen_at = NOW(),
                            user_id      = COALESCE(search_misses.user_id, EXCLUDED.user_id)
                """),
                {
                    "query": query.strip(),
                    "norm":  normalized,
                    "cat":   category,
                    "vmfr":  vehicle_manufacturer,
                    "uid":   user_id,
                },
            )
            await db.commit()
    except Exception as e:
        print(f"[search_miss] log error (non-fatal): {e}")


# ==============================================================================
# CONFIGURATION
# ==============================================================================

# Model selection — HF-backed default model alias
HF_DEFAULT_MODEL = os.getenv("HF_TEXT_MODEL", "Qwen/Qwen2.5-72B-Instruct")

# Aliases kept for backward-compat with agent subclasses that reference these names
LLAMA_8B      = HF_DEFAULT_MODEL
LLAMA_70B     = HF_DEFAULT_MODEL
MISTRAL       = HF_DEFAULT_MODEL
PHI           = HF_DEFAULT_MODEL
VISION_MODEL  = HF_DEFAULT_MODEL
GPT4O_MINI    = HF_DEFAULT_MODEL
GPT4O         = HF_DEFAULT_MODEL
GPT55         = HF_DEFAULT_MODEL
CLAUDE_SONNET = HF_DEFAULT_MODEL
CLAUDE_SONNET_46 = HF_DEFAULT_MODEL

# Defaults — override via .env: AGENTS_DEFAULT_MODEL
FREE_MODEL    = os.getenv("AGENTS_DEFAULT_MODEL", HF_DEFAULT_MODEL)
PREMIUM_MODEL = FREE_MODEL  # one model only

# Business constants
PROFIT_MARGIN = 1.45       # 45% markup on cost
VAT_RATE = 0.18            # 18%
SHIPPING_ILS = 91.0        # default customer delivery fee (₪)
# Import the single source of truth for USD→ILS rate from BACKEND_DATABASE_MODELS
from BACKEND_DATABASE_MODELS import USD_TO_ILS

# Customer-facing delivery fee per supplier (varies by origin country)
SUPPLIER_SHIPPING_RATES: dict = {
    "AutoParts Pro IL": 29.0,     # Israel domestic delivery
    "Global Parts Hub": 91.0,     # Germany / Europe
    "EastAuto Supply":  149.0,    # China / Far East
    "PartsPro USA":     110.0,    # USA → Israel (UPS/FedEx)
    "AutoZone Direct":  120.0,    # USA → Israel (retail shipping)
    "Hyundai Mobis":    95.0,     # South Korea → Israel (OEM direct)
    "Kia Parts Direct": 95.0,     # South Korea → Israel (OEM direct)
    "Bosch Direct":     80.0,     # Germany (manufacturer direct)
    "Toyota Genuine":   99.0,     # Japan → Israel (OEM direct)
}

def get_supplier_shipping(supplier_name: str) -> float:
    """Return the customer-facing delivery fee for a given supplier."""
    return SUPPLIER_SHIPPING_RATES.get(supplier_name, SHIPPING_ILS)


# ==============================================================================
# BASE AGENT
# ==============================================================================

class BaseAgent:
    """Base class for all Auto Spare AI agents."""

    name: str = "base_agent"
    model: str = FREE_MODEL
    system_prompt: str = "You are a helpful assistant for Auto Spare."
    max_tokens: int = 1500
    temperature: float = 0.7

    def __init__(self):
        if not os.getenv("HF_TOKEN", ""):
            print(f"[WARN] {self.name}: HF_TOKEN not set. AI responses will be mocked.")

    async def think(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        system_override: Optional[str] = None,
    ) -> str:
        """Send messages to GitHub Models API and return response text."""
        if not os.getenv("HF_TOKEN", ""):
            return f"[Mock] {self.name} received your message. Please set HF_TOKEN in .env for real AI responses."

        try:
            prompt = "\n".join(
                f"{m.get('role', 'user')}: {m.get('content', '')}"
                for m in messages
            ).strip()
            if not prompt:
                prompt = "Please continue."
            return await hf_text(prompt, system=(system_override or self.system_prompt))
        except Exception as e:
            print(f"[ERROR] {self.name} API call failed: {e}")
            return f"אני מצטער, נתקלתי בבעיה טכנית. אנא נסה שוב."

    def calculate_customer_price(
        self,
        supplier_price_usd: float,
        shipping_cost_usd: float = 0.0,
        customer_shipping: Optional[float] = None,
    ) -> Dict[str, float]:
        """Calculate final customer price from supplier cost (USD).
        customer_shipping overrides the default SHIPPING_ILS delivery fee."""
        cost_ils = (supplier_price_usd + shipping_cost_usd) * USD_TO_ILS
        price_no_vat = round(cost_ils * PROFIT_MARGIN, 2)
        vat = round(price_no_vat * VAT_RATE, 2)
        delivery = customer_shipping if customer_shipping is not None else SHIPPING_ILS
        total = round(price_no_vat + vat + delivery, 2)
        profit = round(price_no_vat - cost_ils, 2)
        return {
            "cost_ils": round(cost_ils, 2),
            "price_no_vat": price_no_vat,
            "vat": vat,
            "shipping": delivery,
            "total": total,
            "profit": profit,
        }

    def calculate_customer_price_from_ils(
        self,
        cost_ils: float,
        shipping_cost_ils: float = 0.0,
        customer_shipping: Optional[float] = None,
    ) -> Dict[str, float]:
        """Calculate final customer price when supplier cost is already in ILS.
        customer_shipping overrides the default SHIPPING_ILS delivery fee."""
        total_cost_ils = cost_ils + shipping_cost_ils
        price_no_vat = round(total_cost_ils * PROFIT_MARGIN, 2)
        vat = round(price_no_vat * VAT_RATE, 2)
        delivery = customer_shipping if customer_shipping is not None else SHIPPING_ILS
        total = round(price_no_vat + vat + delivery, 2)
        profit = round(price_no_vat - total_cost_ils, 2)
        return {
            "cost_ils": round(total_cost_ils, 2),
            "price_no_vat": price_no_vat,
            "vat": vat,
            "shipping": delivery,
            "total": total,
            "profit": profit,
        }

    # ── Shared text-extraction helpers (used by PartsFinderAgent & SalesAgent) ─

    # Hebrew → DB category keyword map
    _CATEGORY_KEYWORDS: Dict[str, str] = {
        "בלמ": "בלמים", "רפידות": "בלמים", "דיסק": "בלמים", "צלחות": "בלמים",
        "קליפר": "בלמים", "רכב בלם": "בלמים",
        "מנוע": "מנוע", "פיסטון": "מנוע", "גל ארכובה": "מנוע", "גל זיזים": "מנוע",
        "טורבו": "מנוע", "מצמד": "מנוע", "ראש מנוע": "מנוע",
        "מתלה": "מתלים והגה", "זרוע": "מתלים והגה", "קפיץ": "מתלים והגה",
        "בולם": "מתלים והגה", "הגה": "מתלים והגה", "טרפז": "מתלים והגה",
        "פנס": "תאורה", "פנסים": "תאורה", "נורה": "תאורה",
        "LED": "תאורה", "בוקר": "גוף ואקסטריור", "פגוש": "גוף ואקסטריור",
        "כנף": "גוף ואקסטריור", "דלת": "גוף ואקסטריור", "מכסה מנוע": "גוף ואקסטריור",
        "מראה": "גוף ואקסטריור",
        "חיישן": "חשמל", "מחוון": "חשמל", "מצתר": "חשמל", "ECU": "חשמל",
        "ממסר": "חשמל", "אלטרנטור": "חשמל", "מצבר": "חשמל",
        "מסנן": "מסננים ושמנים", "פילטר": "מסננים ושמנים", "שמן": "מסננים ושמנים",
        "מיזוג": "מיזוג ומערכת חימום", "AC": "מיזוג ומערכת חימום",
        "קומפרסור": "מיזוג ומערכת חימום", "אוורור": "מיזוג ומערכת חימום",
        "תיבת הילוכים": "תיבת הילוכים", "גיר": "תיבת הילוכים",
        "דלק": "מערכת דלק", "משאבת דלק": "מערכת דלק", "אינג'קטור": "מערכת דלק",
        "קירור": "קירור", "ראדיאטור": "קירור", "טרמוסטט": "קירור",
        "משאבת מים": "קירור", "מאוורר": "קירור",
        "כיסא": "פנים הרכב", "שטיח": "פנים הרכב",
        "פנים": "פנים הרכב", "דשבורד": "פנים הרכב", "כרית אויר": "פנים הרכב",
        "גלגל": "גלגלים וצמיגים", "צמיג": "גלגלים וצמיגים", "ג'אנט": "גלגלים וצמיגים",
        "קטליזטור": "מערכת פליטה", "מאיין": "מערכת פליטה",
        "אטם": "אטמים וחומרים", "גאסקט": "אטמים וחומרים",
        "רצועה": "שרשראות ורצועות", "שרשרת": "שרשראות ורצועות",
        "סרן": "סרן והינע", "כרדן": "סרן והינע", "ג'וינט": "סרן והינע",
        "מגב": "מגבים",
        "ג'ק": "כלים וציוד", "כלי עבודה": "כלים וציוד",
    }

    def _extract_category_hint(self, message: str) -> Optional[str]:
        """Quick keyword-based category detection without LLM call."""
        msg_lower = message.lower()
        for kw, cat in self._CATEGORY_KEYWORDS.items():
            if kw.lower() in msg_lower:
                return cat
        return None

    def _extract_search_query(self, message: str) -> str:
        """Extract a concise search query: strip common Hebrew filler phrases."""
        import re
        filler = [
            r'אני צריך\s*', r'אני רוצה\s*', r'אנחנו צריכים\s*',
            r'אני מחפש\s*', r'אני מחפשת\s*', r'אני מ\w+\s+',  # conjugated: ממסנן, מחפש etc.
            r'מה המחיר של\s*', r'כמה עולה\s*', r'כמה עולות\s*',
            r'תוכל לבדוק\s*', r'בדוק\s*',
            r'חפש\s*', r'יש לכם\s*', r'יש לך\s*',
            r'לרכב שלי\s*', r'עבור הרכב שלי\s*',
            r'רכבי הוא\s+\S+\s+\d{4}[^,–-]*[,–-]?\s*',
        ]
        cleaned = message
        for f in filler:
            cleaned = re.sub(f, '', cleaned, flags=re.IGNORECASE).strip()
        return cleaned[:60].strip()


# ==============================================================================
# 0. ROUTER AGENT
# ==============================================================================

class RouterAgent(BaseAgent):
    name = "router_agent"
    agent_name = "Avi"          # אבי — the smart dispatcher
    model = FREE_MODEL          # routing is simple — free tier is fine
    temperature = 0.1  # deterministic routing
    system_prompt = """You are Avi, the routing agent for Auto Spare, an Israeli auto parts dropshipping platform.

Your ONLY job is to identify which specialized agent should handle the user's message.

Available agents:
- parts_finder_agent: License plate lookup, VIN/OEM number identification, part identification from image or audio description. VIN search, barcode scans, and image uploads all route here. Do NOT use for general part price or availability questions.
- sales_agent: Any customer inquiry about a specific part — price questions ("כמה עולה X?"), availability ("יש לכם X?"), part search by name or type, Good/Better/Best recommendations, upselling, bundles, purchasing decisions. Use this for ANY "looking for a part" message.
- orders_agent: Order status, tracking, cancellations, returns AND payment/checkout questions ("אפשר לשלם?", "איך משלמים?", "לינק לתשלום", "להשלים הזמנה"). Route ANY payment or checkout question here. Also handle abandoned cart questions — customer mentions items they added but did not purchase.
- finance_agent: Invoice requests, VAT breakdowns, refund calculations, billing disputes — NOT for payment links or checkout flow.
- service_agent: Technical support, complaints, general questions, after-sales. Also handles: wishlist questions ("רשימת משאלות", "שמור לרשימה"), product reviews, and audio/image upload errors.
- security_agent: Login issues, 2FA, password reset, account security, suspicious activity
- marketing_agent: Promotions, coupons, discounts, newsletter, referrals, loyalty points
- social_media_manager_agent: Social media content, posts (admin only)
- supplier_manager_agent: Supplier catalog, price updates (admin only)

Respond ONLY with valid JSON in this exact format:
{
  "agent": "agent_name_here",
  "confidence": 0.95,
  "language": "he",
  "intent": "brief_intent_description",
  "extracted_data": {}
}

Language should be the detected language of the message (he=Hebrew, ar=Arabic, en=English, etc.)
IMPORTANT: Default to "he" (Hebrew) if the message contains only numbers, order codes, part numbers, or mixed/unclear text. This is an Israeli platform — always assume Hebrew unless the message is clearly in Arabic or English.
"""

    async def route(self, message: str, context: Dict = None) -> Dict[str, Any]:
        """Route message to the appropriate agent."""
        response = await self.think([{"role": "user", "content": message}])
        try:
            # Extract JSON from response
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except Exception:
            pass
        # Default fallback
        return {
            "agent": "service_agent",
            "confidence": 0.5,
            "language": "he",
            "intent": "general_query",
            "extracted_data": {},
        }


# ==============================================================================
# 1. PARTS FINDER AGENT
# ==============================================================================

class PartsFinderAgent(BaseAgent):
    name = "parts_finder_agent"
    agent_name = "Nir"          # ניר — the parts expert
    model = PREMIUM_MODEL       # premium: complex Hebrew part-matching & pricing
    system_prompt = """You are Nir, the Parts Finder Agent for Auto Spare, an Israeli auto parts platform.

CRITICAL RULES:
1. NEVER mention supplier names (RockAuto, FCP Euro, Autodoc, AliExpress) to customers
2. ALWAYS show manufacturer of the part (Bosch, Brembo, Toyota OEM, etc.)
3. ALWAYS show price breakdown: net price + VAT (18%) + shipping (₪29–₪149 לפי ספק — הצג הטווח אם לא ידוע מחיר מדויק)
4. Results must be sorted by MANUFACTURER, not by supplier
5. LANGUAGE: ALWAYS respond in Hebrew (עברית). If the customer writes in Arabic, respond in Arabic. Never respond in any other language — if the message is just a part number or vehicle code, respond in Hebrew.
6. Always include warranty period and delivery estimate
7. DROPSHIPPING: This is a 100% dropshipping system — no physical warehouse. Say "זמין להזמנה" (available to order), NEVER "יש במלאי" (in stock). Parts ship from our supplier after customer payment.

PART CATEGORIES IN DB (use these exact 14 values for category filters):
- בלמים          → brake discs, pads, calipers, cylinders
- גלגלים וצמיגים → wheels, tyres, rims
- דלק            → fuel pumps, injectors, carburettors
- היגוי          → steering arms, rack, pump, tie-rods
- חשמל רכב       → sensors, starters, alternators, ECU, relays
- כללי           → uncategorised parts
- מגבים          → wipers, washer jets, washer reservoir
- מיזוג          → AC compressors, evaporators, heaters
- מנוע           → engine internals, pistons, timing, turbo, oil/air/fuel filters
- מתלה           → suspension, shocks, springs
- פחיין ומרכב    → bumpers, doors, fenders, hoods, mirrors, body panels
- ריפוד ופנים    → seats, trim, carpets, airbags, dashboards
- שרשראות ורצועות → timing belts/chains, drive belts, pulleys, tensioners
- תאורה          → headlights, tail-lights, bulbs, LEDs

VEHICLE BRANDS WITH STOCK: Do NOT use a hard-coded list. Call get_db_stats() to get the current list of manufacturers with stock from the live database. The brand list changes as new suppliers are added.

PART TYPES: Original | OEM | Aftermarket

Price format example:
✅ [Original] Renault
   קטגוריה: בלמים
   מחיר: 520 ₪ + 88 ₪ מע"מ = 608 ₪
   משלוח: ₪29–₪149 (לפי ספק)
   סה"כ: 699 ₪
   אחריות: 24 חודשים
   זמן אספקה: 10-14 ימים

You have access to database search functions. When identifying a vehicle by license plate,
use the Israeli Transport Ministry API format. You LEARN from the live database — use
get_db_stats() to verify what categories and manufacturers currently hold stock.

CROSS-REFERENCE: Alternative/equivalent part numbers are stored in the part_cross_reference table. When a customer provides an OEM number, always check cross-references and offer equivalent parts from other manufacturers if available.
"""

    # Real part categories as classified in the DB (matches fix_db_quality.py rules)
    KNOWN_CATEGORIES: list[str] = [
        "בלמים", "גלגלים וצמיגים", "דלק", "היגוי", "חשמל רכב",
        "כללי", "מגבים", "מיזוג", "מנוע", "מתלה",
        "פחיין ומרכב", "ריפוד ופנים", "שרשראות ורצועות", "תאורה",
    ]

    # In-memory stats cache with TTL of 3600 seconds (1 hour)
    _stats_cache: dict = {}
    _stats_loaded_at: float = 0.0
    _STATS_TTL: float = 3600.0

    async def get_db_stats(self, db: AsyncSession) -> Dict:
        """
        Learn from the live DB: returns category counts, manufacturer counts,
        part-type breakdown, and total active parts.
        The agent can call this periodically to stay up to date.
        """
        from sqlalchemy import func
        # Always use catalog DB — PartsCatalog lives in autospare, not pii
        async with async_session_factory() as cat_db:
            cat_result = await cat_db.execute(
                select(PartsCatalog.category, func.count(PartsCatalog.id).label("cnt"))
                .where(PartsCatalog.is_active == True)
                .group_by(PartsCatalog.category)
                .order_by(func.count(PartsCatalog.id).desc())
            )
            mfr_result = await cat_db.execute(
                select(PartsCatalog.manufacturer, func.count(PartsCatalog.id).label("cnt"))
                .where(PartsCatalog.is_active == True)
                .group_by(PartsCatalog.manufacturer)
                .order_by(func.count(PartsCatalog.id).desc())
            )
            pt_result = await cat_db.execute(
                select(PartsCatalog.part_type, func.count(PartsCatalog.id).label("cnt"))
                .where(PartsCatalog.is_active == True)
                .group_by(PartsCatalog.part_type)
                .order_by(func.count(PartsCatalog.id).desc())
            )
            total_result = await cat_db.execute(
                select(func.count(PartsCatalog.id)).where(PartsCatalog.is_active == True)
            )
            return {
                "total_active": total_result.scalar(),
                "categories": {row[0]: row[1] for row in cat_result.fetchall()},
                "manufacturers": {row[0]: row[1] for row in mfr_result.fetchall()},
                "part_types": {row[0]: row[1] for row in pt_result.fetchall()},
            }

    async def normalize_manufacturer(self, raw_name: str, db: AsyncSession) -> str:
        """Normalize a raw manufacturer string to the canonical car_brands or truck_brands name.
        Checks: exact name, Hebrew name, aliases array — in both tables.
        Falls back to original string if no match.
        """
        if not raw_name or not raw_name.strip():
            return raw_name
        cleaned = raw_name.strip()
        # Always use catalog DB — CarBrand/TruckBrand live in autospare, not pii
        async with async_session_factory() as cat_db:
            for Model, aliases_col in (
                (CarBrand, "car_brands.aliases"),
                (TruckBrand, "truck_brands.aliases"),
            ):
                # 1. Exact match on name or name_he
                result = await cat_db.execute(
                    select(Model.name).where(Model.is_active == True).where(
                        or_(Model.name.ilike(cleaned), Model.name_he.ilike(cleaned))
                    ).limit(1)
                )
                row = result.scalar_one_or_none()
                if row:
                    return row
                # 2. Check aliases array (text[] in DB — use ANY operator)
                result2 = await cat_db.execute(
                    select(Model.name).where(Model.is_active == True).where(
                        text(f"(:val)::text = ANY({aliases_col})")
                    ).params(val=cleaned).limit(1)
                )
                row2 = result2.scalar_one_or_none()
                if row2:
                    return row2
                # 3. Prefix / substring match
                result3 = await cat_db.execute(
                    select(Model.name).where(Model.is_active == True).where(
                        or_(
                            Model.name.ilike(f"{cleaned}%"),
                            Model.name.ilike(f"%{cleaned}%"),
                        )
                    ).order_by(Model.name).limit(1)
                )
                row3 = result3.scalar_one_or_none()
                if row3:
                    return row3
            return cleaned

    async def list_known_brands(self, db: AsyncSession) -> List[Dict]:
        """Return all active brands from car_brands (passenger) and truck_brands registries."""
        # Always use catalog DB — CarBrand/TruckBrand live in autospare, not pii
        async with async_session_factory() as cat_db:
            car_result = await cat_db.execute(
                select(CarBrand).where(CarBrand.is_active == True).order_by(CarBrand.name)
            )
            truck_result = await cat_db.execute(
                select(TruckBrand).where(TruckBrand.is_active == True).order_by(TruckBrand.name)
            )
            car_brands = car_result.scalars().all()
            truck_brands = truck_result.scalars().all()
            output: List[Dict] = [
                {
                    "name": b.name,
                    "name_he": b.name_he,
                    "group": b.group_name,
                    "country": b.country,
                    "region": b.region,
                    "is_luxury": b.is_luxury,
                    "is_electric": b.is_electric_focused,
                    "vehicle_type": "car",
                }
                for b in car_brands
            ]
            output += [
                {
                    "name": b.name,
                    "name_he": b.name_he,
                    "group": b.group_name,
                    "country": b.country,
                    "region": b.region,
                    "is_luxury": False,
                    "is_electric": False,
                    "vehicle_type": "truck",
                }
                for b in truck_brands
            ]
            return output

    @staticmethod
    def _vehicle_response(vehicle: "Vehicle", extra: dict | None = None) -> Dict:
        """Build the standard vehicle response dict from a Vehicle ORM row."""
        gov = vehicle.gov_api_data or {}
        return {
            "id": str(vehicle.id),
            "license_plate": vehicle.license_plate,
            "manufacturer": vehicle.manufacturer,
            "model": vehicle.model,
            "year": vehicle.year,
            "engine_type": vehicle.engine_type,
            "fuel_type": vehicle.fuel_type or gov.get("fuel_type"),
            "color": gov.get("color"),
            "transmission": vehicle.transmission or gov.get("transmission"),
            "engine_cc": gov.get("engine_cc"),
            "horsepower": gov.get("horsepower"),
            "vehicle_type": gov.get("vehicle_type"),
            "doors": gov.get("doors"),
            "seats": gov.get("seats"),
            "front_tire": gov.get("front_tire"),
            "rear_tire": gov.get("rear_tire"),
            "emissions_group": gov.get("emissions_group"),
            "last_test_date": gov.get("last_test_date"),
            "test_expiry_date": gov.get("test_expiry_date"),
            "ownership": gov.get("ownership"),
            "country_of_origin": gov.get("country_of_origin"),
            **(extra or {}),
        }

    async def identify_vehicle(self, license_plate: str, db: AsyncSession) -> Dict:
        """Identify vehicle from license plate via Israeli Transport Ministry API (data.gov.il).
        Vehicle is Base (catalog DB) — uses async_session_factory.
        The `db` parameter is kept for API compatibility but is not used.
        """
        clean_plate = license_plate.replace("-", "").replace(" ", "")

        async with async_session_factory() as pii_db:
            # Check DB cache (90-day TTL)
            result = await pii_db.execute(
                select(Vehicle).where(Vehicle.license_plate == clean_plate)
            )
            vehicle = result.scalar_one_or_none()

            if vehicle and vehicle.cached_at:
                cache_age = (datetime.utcnow() - vehicle.cached_at).days
                if cache_age < 90:
                    return self._vehicle_response(vehicle)

            # Live call to data.gov.il
            vehicle_data = await self._call_gov_api(clean_plate)
            if not vehicle_data:
                raise Exception(f"Vehicle with plate {clean_plate} not found in government database")

            # Strip internal _raw key before persisting to avoid large JSONB
            raw = vehicle_data.pop("_raw", {})
            gov_cache = {**vehicle_data, "_raw_fields": list(raw.keys())}

            # Persist / update
            if vehicle:
                vehicle.manufacturer    = vehicle_data.get("manufacturer") or vehicle.manufacturer
                vehicle.model           = vehicle_data.get("model") or vehicle.model
                vehicle.year            = vehicle_data.get("year") or vehicle.year
                vehicle.engine_type     = vehicle_data.get("engine_type") or vehicle.engine_type
                vehicle.fuel_type       = vehicle_data.get("fuel_type") or vehicle.fuel_type
                vehicle.transmission    = vehicle_data.get("transmission") or vehicle.transmission
                vehicle.gov_api_data    = gov_cache
                vehicle.cached_at       = datetime.utcnow()
            else:
                vehicle = Vehicle(
                    license_plate   = clean_plate,
                    manufacturer    = vehicle_data.get("manufacturer", ""),
                    model           = vehicle_data.get("model", ""),
                    year            = vehicle_data.get("year", 0),
                    engine_type     = vehicle_data.get("engine_type"),
                    fuel_type       = vehicle_data.get("fuel_type"),
                    transmission    = vehicle_data.get("transmission"),
                    gov_api_data    = gov_cache,
                    cached_at       = datetime.utcnow(),
                )
                pii_db.add(vehicle)

            await pii_db.commit()
            await pii_db.refresh(vehicle)
            return self._vehicle_response(vehicle)

    # data.gov.il resource IDs (Ministry of Transport – private & commercial vehicles)
    _GOV_RESOURCES = [
        # Primary: full private/commercial vehicles database (updated daily)
        "053cea08-09bc-40ec-8f7a-156f0677aff3",
        # Secondary: detailed ministry dataset (additional tire/weight fields)
        "bf9df4e2-d90d-4c0a-a400-19e15af8e95c",
    ]
    _GOV_URL = "https://data.gov.il/api/3/action/datastore_search"

    @staticmethod
    def _map_gov_record(r: dict, clean_plate: str) -> Dict:
        """Map a raw data.gov.il vehicle record to our internal schema."""
        def s(key): return (str(r.get(key) or "")).strip() or None
        def i(key):
            try: return int(r.get(key) or 0)
            except: return 0
        def f(key):
            try: return float(r.get(key) or 0)
            except: return 0.0

        manufacturer = s("tozeret_nm") or s("tozeret_cd") or "Unknown"
        model        = s("kinuy_mishari") or s("degem_nm") or "Unknown"
        year         = i("shnat_yitzur")
        fuel_type    = s("sug_delek_nm")
        color        = s("tzeva_rechev")
        engine_cc    = i("nefach_manoa")          # engine displacement cc
        engine_model = s("degem_manoa")            # engine model name
        vehicle_type = s("sug_rechev_nm")          # e.g. פרטי, מסחרי
        doors        = i("mispar_dlatot")
        seats        = i("mispar_moshavim")
        total_weight = i("mishkal_kolel")          # total weight kg
        reg_weight   = i("mishkal_atzmi")          # self weight kg
        last_test    = s("mivchan_acharon_dt")
        test_expiry  = s("tokef_dt")
        ownership    = s("baalut")                 # private / leased / etc.
        country_of_origin = s("medinatkone")
        front_tire   = s("zmig_kidmi")
        rear_tire    = s("zmig_ahori")
        emissions_group = s("kvutzat_zihum")
        horsepower   = i("koah_sus")

        # Build a human-readable engine string if we have the data
        if engine_cc and engine_cc > 0:
            engine_type = f"{fuel_type or 'Unknown'} {engine_cc}cc"
        else:
            engine_type = fuel_type or engine_model

        # Transmission guess from vehicle data
        transmission = s("teur_hibbur")

        return {
            "license_plate": clean_plate,
            "manufacturer": manufacturer,
            "model": model,
            "year": year,
            "engine_type": engine_type,
            "fuel_type": fuel_type,
            "color": color,
            "transmission": transmission,
            "engine_cc": engine_cc,
            "engine_model": engine_model,
            "horsepower": horsepower,
            "vehicle_type": vehicle_type,
            "doors": doors,
            "seats": seats,
            "total_weight_kg": total_weight,
            "self_weight_kg": reg_weight,
            "front_tire": front_tire,
            "rear_tire": rear_tire,
            "emissions_group": emissions_group,
            "last_test_date": last_test,
            "test_expiry_date": test_expiry,
            "ownership": ownership,
            "country_of_origin": country_of_origin,
            "_raw": r,
        }

    @retry_with_backoff(max_retries=2, base_delay=1.0, max_delay=30.0, retry_on=(429, 503, 504))
    async def _call_gov_api(self, license_plate: str) -> Optional[Dict]:
        """Call Israeli Transport Ministry API (data.gov.il) with dual-source fallback."""
        clean_plate = license_plate.replace("-", "").replace(" ", "")
        # Also try zero-padded 7-digit format
        plates_to_try = [clean_plate]
        if clean_plate.isdigit() and len(clean_plate) < 8:
            plates_to_try.append(clean_plate.zfill(7))
            plates_to_try.append(clean_plate.zfill(8))

        async with httpx.AsyncClient(timeout=12.0) as client:
            for resource_id in self._GOV_RESOURCES:
                for plate in plates_to_try:
                    try:
                        resp = await client.get(
                            self._GOV_URL,
                            params={
                                "resource_id": resource_id,
                                "filters": json.dumps({"mispar_rechev": plate}),
                                "limit": 1,
                            },
                        )
                        resp.raise_for_status()
                        records = resp.json().get("result", {}).get("records", [])
                        if records:
                            print(f"[GOV_API] Found plate {plate} in resource {resource_id}")
                            return self._map_gov_record(records[0], clean_plate)
                    except Exception as e:
                        print(f"[GOV_API] resource={resource_id} plate={plate} error: {e}")
                        continue

        print(f"[GOV_API] Plate {clean_plate} not found in any resource")
        return None

    @retry_with_backoff(max_retries=2, base_delay=1.0, max_delay=30.0, retry_on=(429, 503, 504))
    async def search_parts_in_db(
        self,
        query: str,
        vehicle_id: Optional[str],
        category: Optional[str],
        db: AsyncSession,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "name",
        sort_dir: str = "asc",
        vehicle_manufacturer: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict]:
        """Search parts catalog.
        Text search is powered by Meilisearch when available; falls back to ILIKE.
        Automatically normalizes manufacturer aliases via car_brands registry
        (e.g. 'מרצדס' → 'Mercedes', 'מרצדס בנץ' → 'Mercedes-Benz').

        sort_by options: name, manufacturer, category, part_type, price_asc, price_desc
        sort_dir: asc | desc  (ignored when sort_by is price_asc/price_desc)
        """
        # ── Meilisearch text lookup (optional) ──────────────────────────────
        # meili_ids: List[str]  → ranked UUIDs → use unnest JOIN, skip ILIKE
        # meili_ids: None       → Meilisearch unavailable → fall back to ILIKE
        # meili_ids: []         → zero hits → short-circuit
        meili_ids: Optional[List[str]] = None
        _meili_url = os.getenv("MEILI_URL", "")
        if query and _meili_url:
            try:
                async with httpx.AsyncClient(timeout=2.0) as _mc:
                    _resp = await _mc.post(
                        f"{_meili_url}/indexes/parts/search",
                        headers={"Authorization": f"Bearer {os.getenv('MEILI_MASTER_KEY', '')}"},
                        json={"q": query, "limit": 200, "attributesToRetrieve": ["id"]},
                    )
                    _resp.raise_for_status()
                    meili_ids = [h["id"] for h in _resp.json().get("hits", [])]
            except Exception:
                meili_ids = None  # fall back to ILIKE silently

        # ── Short-circuit: Meilisearch found zero hits ───────────────────────
        if meili_ids is not None and len(meili_ids) == 0:
            asyncio.create_task(_log_search_miss(query, category, vehicle_manufacturer, user_id))
            return []

        # ── pgvector: embed the query and find nearest neighbours ────────────
        # vec_score: {id_str → cosine_similarity}  (empty if unavailable)
        # Runs only when Meilisearch returned results.
        # if Meilisearch already short-circuited or fell back to ILIKE.
        vec_score: Dict[str, float] = {}
        if meili_ids and query:
            try:
                query_vec: Optional[List[float]] = await hf_embed(query, timeout=3.0)

                if query_vec:
                    async with async_session_factory() as _vdb:
                        _vrows = (await _vdb.execute(
                            text("""
                                SELECT id::text,
                                       1 - (embedding <=> CAST(:qvec AS vector)) AS sim
                                FROM parts_catalog
                                WHERE is_active = TRUE
                                  AND embedding IS NOT NULL
                                ORDER BY embedding <=> CAST(:qvec AS vector)
                                LIMIT 50
                            """),
                            {"qvec": str(query_vec)},
                        )).fetchall()
                    vec_score = {r[0]: float(r[1]) for r in _vrows}
            except Exception:
                vec_score = {}  # degrade silently to Meilisearch-only

        # ── Hybrid re-rank: 0.6 × meili_score + 0.4 × vec_score ─────────────
        # meili_score for rank i (0-based): 1/(i+1) → rank 0=1.0, rank 1=0.5 …
        # vec_score: cosine similarity (1 − distance) → higher = more similar
        # IDs absent from one source receive 0.0 for that source.
        if vec_score:
            meili_scores = {uid: 1.0 / (i + 1) for i, uid in enumerate(meili_ids)}
            all_ids = list(dict.fromkeys(list(meili_scores) + list(vec_score)))
            combined = {
                uid: 0.6 * meili_scores.get(uid, 0.0) + 0.4 * vec_score.get(uid, 0.0)
                for uid in all_ids
            }
            meili_ids = sorted(combined, key=combined.__getitem__, reverse=True)

        # ── Meilisearch / hybrid path: raw SQL with rank-preserving unnest ───
        if meili_ids is not None:
            conditions = ["pc.is_active = TRUE"]
            params: Dict[str, Any] = {}

            if category:
                conditions.append("pc.category ILIKE :cat")
                params["cat"] = f"%{category}%"

            if vehicle_manufacturer:
                normalized_mfr = await self.normalize_manufacturer(vehicle_manufacturer, db)
                mfr_terms = list({vehicle_manufacturer, normalized_mfr})
                for i, t in enumerate(mfr_terms):
                    conditions.append(f"pc.manufacturer ILIKE :mfr{i}")
                    params[f"mfr{i}"] = f"%{t}%"

            if vehicle_id:
                conditions.append(
                    "(pc.compatible_vehicles::text ILIKE :vid "
                    "OR EXISTS (SELECT 1 FROM part_vehicle_fitment pvf "
                    "           WHERE pvf.part_id = pc.id AND pvf.vehicle_id = :vid_exact))"
                )
                params["vid"] = f"%{vehicle_id}%"
                params["vid_exact"] = vehicle_id

            where_sql = " AND ".join(conditions)
            _dir_sql = "ASC" if sort_dir == "asc" else "DESC"

            # Defensive guard: dynamic SQL fragments must remain clause-only.
            _unsafe_sql_tokens = (";", "--", "/*", "*/")
            if any(tok in where_sql for tok in _unsafe_sql_tokens):
                raise ValueError("Unsafe WHERE fragment detected")

            if sort_by in ("price_asc", "price_desc"):
                order_sql = (
                    "ORDER BY (SELECT MIN(price_usd) FROM supplier_parts "
                    "          WHERE part_id = pc.id) "
                    + ("ASC NULLS LAST" if sort_by == "price_asc" else "DESC NULLS FIRST")
                )
            elif sort_by in ("manufacturer", "category", "part_type"):
                _col_map = {
                    "manufacturer": "pc.manufacturer",
                    "category":     "pc.category",
                    "part_type":    "pc.part_type",
                }
                order_sql = f"ORDER BY ranked.pos ASC, {_col_map[sort_by]} {_dir_sql}"
            else:
                order_sql = "ORDER BY ranked.pos ASC"

            if any(tok in order_sql for tok in _unsafe_sql_tokens):
                raise ValueError("Unsafe ORDER BY fragment detected")

            # Pass as Python list so asyncpg maps it correctly to PostgreSQL text[].
            params["uuid_arr"] = meili_ids
            params["lim"] = limit
            params["off"] = offset

            async with async_session_factory() as cat_db:
                rows = (await cat_db.execute(
                    text(f"""
                        SELECT pc.*
                        FROM parts_catalog pc
                        JOIN (
                            SELECT t.id::uuid AS ranked_id, t.pos
                            FROM unnest(CAST(:uuid_arr AS text[])) WITH ORDINALITY AS t(id, pos)
                        ) ranked ON ranked.ranked_id = pc.id
                        WHERE {where_sql}
                        {order_sql}
                        LIMIT :lim OFFSET :off
                    """),
                    params,
                )).fetchall()

            from types import SimpleNamespace
            parts = [SimpleNamespace(**dict(r._mapping)) for r in rows]

        else:
            # ── ILIKE fallback path (original logic, unchanged) ───────────────
            stmt = select(PartsCatalog).where(PartsCatalog.is_active == True)

            if vehicle_manufacturer:
                normalized_mfr = await self.normalize_manufacturer(vehicle_manufacturer, db)
                # Also try splitting compound Hebrew names like "סיטרואן ספרד" → try each word
                words = vehicle_manufacturer.split()
                word_normalized = normalized_mfr
                for word in words:
                    if len(word) >= 3:
                        candidate = await self.normalize_manufacturer(word, db)
                        if candidate.lower() != word.lower():  # successfully resolved
                            word_normalized = candidate
                            break
                mfr_terms = {vehicle_manufacturer, normalized_mfr, word_normalized}
                stmt = stmt.where(or_(*[PartsCatalog.manufacturer.ilike(f"%{t}%") for t in mfr_terms]))

            if query:
                normalized = await self.normalize_manufacturer(query, db)
                search_terms = {query, normalized} if normalized.lower() != query.lower() else {query}
                conditions = []
                for term in search_terms:
                    conditions += [
                        PartsCatalog.name.ilike(f"%{term}%"),
                        PartsCatalog.manufacturer.ilike(f"%{term}%"),
                        PartsCatalog.sku.ilike(f"%{term}%"),
                        PartsCatalog.category.ilike(f"%{term}%"),
                    ]
                stmt = stmt.where(or_(*conditions))

            if category:
                stmt = stmt.where(PartsCatalog.category.ilike(category))

            _dir = lambda col: col.asc() if sort_dir == "asc" else col.desc()
            if sort_by in ("price_asc", "price_desc"):
                price_subq = (
                    select(SupplierPart.part_id, func.min(SupplierPart.price_usd).label("min_price"))
                    .group_by(SupplierPart.part_id).subquery()
                )
                stmt = stmt.outerjoin(price_subq, PartsCatalog.id == price_subq.c.part_id)
                if sort_by == "price_asc":
                    stmt = stmt.order_by(price_subq.c.min_price.asc().nullslast())
                else:
                    stmt = stmt.order_by(price_subq.c.min_price.desc().nullsfirst())
            elif sort_by == "availability":
                avail_subq = (
                    select(SupplierPart.part_id, func.bool_or(SupplierPart.is_available).label("has_stock"))
                    .where(SupplierPart.part_id.in_(select(PartsCatalog.id).where(PartsCatalog.is_active == True)))
                    .group_by(SupplierPart.part_id).subquery()
                )
                stmt = stmt.outerjoin(avail_subq, PartsCatalog.id == avail_subq.c.part_id)
                stmt = stmt.order_by(avail_subq.c.has_stock.desc().nullslast(), PartsCatalog.name.asc())
            elif sort_by == "manufacturer":
                stmt = stmt.order_by(_dir(PartsCatalog.manufacturer))
            elif sort_by == "category":
                stmt = stmt.order_by(_dir(PartsCatalog.category))
            elif sort_by == "part_type":
                stmt = stmt.order_by(_dir(PartsCatalog.part_type))
            else:  # default: name
                stmt = stmt.order_by(_dir(PartsCatalog.name))

            async with async_session_factory() as cat_db:
                result = await cat_db.execute(stmt.offset(offset).limit(limit))
                parts = result.scalars().all()

        if not parts:
            asyncio.create_task(_log_search_miss(query, category, vehicle_manufacturer, user_id))
            return []

        # Batch fetch best supplier_part for all parts in 2 queries
        # (avoids N+1 queries — critical for 50-result pages)
        part_ids = [part.id for part in parts]

        # Single query: DISTINCT ON (part_id) — best supplier per part, in_stock first
        async with async_session_factory() as cat_db:
            sp_batch_result = await cat_db.execute(
                text("""
                    SELECT DISTINCT ON (sp.part_id)
                        sp.id AS sp_id, sp.part_id, sp.price_usd, sp.shipping_cost_usd,
                        sp.is_available, sp.warranty_months, sp.estimated_delivery_days,
                        s.name AS supplier_name, s.country AS supplier_country,
                        ROW_NUMBER() OVER (
                            PARTITION BY sp.part_id
                            ORDER BY sp.is_available DESC, s.priority ASC
                        ) AS rn
                    FROM supplier_parts sp
                    JOIN suppliers s ON sp.supplier_id = s.id
                    WHERE sp.part_id = ANY(:pids) AND s.is_active = true
                """),
                {"pids": part_ids},
            )
            sp_rows_all = sp_batch_result.fetchall()
        # Group up to 3 suppliers per part (in_stock first, then on_order)
        from collections import defaultdict
        sp_map: dict[str, list] = defaultdict(list)
        for row in sp_rows_all:
            if row.rn <= 3:
                sp_map[str(row.part_id)].append(row)

        output = []
        for part in parts:
            rows = sp_map.get(str(part.id), [])

            suppliers = []
            for sp_row in rows:
                availability = "in_stock" if sp_row.is_available else "on_order"
                pricing = self.calculate_customer_price(
                    float(sp_row.price_usd),
                    float(sp_row.shipping_cost_usd or 0),
                )
                pricing["availability"] = availability
                pricing["warranty_months"] = sp_row.warranty_months
                pricing["estimated_delivery_days"] = sp_row.estimated_delivery_days
                pricing["supplier_part_id"] = str(sp_row.sp_id)
                pricing["estimated_delivery"] = f"{sp_row.estimated_delivery_days}–{sp_row.estimated_delivery_days + 7} ימים"
                suppliers.append(pricing)

            # Fallback: synthesise pricing from base_price when no supplier row exists
            bp = float(part.base_price) if part.base_price else 0.0
            if not suppliers and bp > 0:
                price_no_vat = round(bp / 1.18, 2)
                vat            = round(bp - price_no_vat, 2)
                shipping       = 35.0
                suppliers = [{
                    "price_no_vat": price_no_vat,
                    "vat": vat,
                    "shipping": shipping,
                    "total": round(bp + shipping, 2),
                    "availability": "on_order",
                    "warranty_months": 12,
                    "estimated_delivery_days": 14,
                    "supplier_part_id": None,
                    "estimated_delivery": "14\u201321 \u05d9\u05de\u05d9\u05dd",
                    "cost_ils": round(bp / 1.45, 2),
                    "profit": round(price_no_vat - round(bp / 1.45, 2), 2),
                    "is_base_price_fallback": True,
                }]

            # Best option = first supplier (in_stock best price)
            best = suppliers[0] if suppliers else None

            output.append({
                "id": str(part.id),
                "name": part.name,
                "manufacturer": part.manufacturer,
                "category": part.category,
                "part_type": part.part_type,
                "description": part.description,
                "sku": part.sku,
                "compatible_vehicles": part.compatible_vehicles or [],
                "pricing": best,           # kept for backward compat
                "suppliers": suppliers,    # all options (up to 3)
                "base_price": bp,
                "warranty_months": rows[0].warranty_months if rows else 12,
            })

        return output

    # _CATEGORY_KEYWORDS, _extract_category_hint, _extract_search_query
    # are inherited from BaseAgent — defined there so SalesAgent can share them.

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        """Process a parts-related message with real DB search integration."""
        import time
        import re
        now = time.time()

        # ── Refresh hourly DB stats cache ──────────────────────────────────────
        if now - self._stats_loaded_at > self._STATS_TTL:
            try:
                stats = await self.get_db_stats(db)
                self.__class__._stats_cache = stats
                self.__class__._stats_loaded_at = now
            except Exception as e:
                print(f"[PartsFinderAgent] get_db_stats failed: {e}")

        # ── STEP 1: License plate identification ───────────────────────────────
        vehicle_context = ""
        identified_vehicle: Optional[Dict] = None   # shared with step 2
        plate_match = re.search(r'(?<!\d)(\d[\d\-]{4,8}\d)(?!\d)', message)
        if plate_match:
            plate_raw = plate_match.group(1).replace("-", "")
            try:
                identified_vehicle = await self.identify_vehicle(plate_raw, db)
                vehicle_context = (
                    f"\n\n[VEHICLE FROM PLATE {plate_raw}]\n"
                    f"יצרן: {identified_vehicle.get('manufacturer')} | דגם: {identified_vehicle.get('model')} | "
                    f"שנה: {identified_vehicle.get('year')} | מנוע: {identified_vehicle.get('engine_type')} | "
                    f"דלק: {identified_vehicle.get('fuel_type')} | צבע: {identified_vehicle.get('color', 'לא ידוע')}\n"
                    f"בדיקה אחרונה: {identified_vehicle.get('last_test_date', 'לא ידוע')} | "
                    f"תוקף רישיון: {identified_vehicle.get('test_expiry_date', 'לא ידוע')}\n"
                )
                print(f"[PartsFinderAgent] Identified plate {plate_raw}: "
                      f"{identified_vehicle.get('manufacturer')} {identified_vehicle.get('model')} {identified_vehicle.get('year')}")
            except Exception as e:
                vehicle_context = f"\n\n[PLATE {plate_raw}: לא נמצא ברישוי ({e})]\n"

        # ── STEP 2: DB parts search ────────────────────────────────────────────
        parts_context = ""
        # Determine if this is a parts search (not just a greeting)
        is_parts_request = any(kw in message for kw in [
            "חלק", "חלקים", "מחיר", "כמה עולה", "יש לכם", "חפש", "מצא",
            "בלמ", "רפידות", "מנוע", "מתלה", "פנס", "מסנן", "פילטר",
            "מגב", "גלגל", "צמיג", "מראה", "מיזוג", "חיישן", "אטם",
            "קירור", "דלק", "גיר", "סרן", "רצועה", "שרשרת",
        ]) or plate_match

        if is_parts_request:
            try:
                # Quick keyword-based category hint
                category_hint = self._extract_category_hint(message)
                search_q = self._extract_search_query(message)

                # For plate-only queries (nothing beside the plate number),
                # use vehicle_manufacturer param so normalize_manufacturer runs on each word.
                # Putting "סיטרואן ספרד BERLINGO" into query never matches "Citroën" in DB.
                plate_only_vehicle_mfr = None
                if identified_vehicle:
                    msg_without_plate = message.replace(plate_match.group(1), "").strip(" -:")
                    if len(msg_without_plate) < 3:
                        plate_only_vehicle_mfr = identified_vehicle.get("manufacturer", "")
                        mod = identified_vehicle.get("model", "")
                        # Use model as query, manufacturer via dedicated param for proper normalisation
                        search_q = mod if mod else ""
                        print(f"[PartsFinderAgent] Plate-only query → mfr='{plate_only_vehicle_mfr}' model='{mod}'")

                print(f"[PartsFinderAgent] Searching: query='{search_q}' category='{category_hint}'")

                results = await self.search_parts_in_db(
                    query=search_q,
                    vehicle_id=None,
                    category=category_hint,
                    db=db,
                    limit=6,
                    sort_by="price_asc",
                    vehicle_manufacturer=plate_only_vehicle_mfr,
                )

                if results:
                    lines = [
                        f"\n[DB SEARCH RESULTS — {len(results)} חלקים נמצאו | "
                        f"קטגוריה: {category_hint or 'כל הקטגוריות'} | "
                        f"חיפוש: '{search_q}']\n"
                        "השתמש ONLY בנתונים אלו — אל תמציא מחירים!\n"
                    ]
                    for i, p in enumerate(results, 1):
                        pr = p.get("pricing") or {}
                        avail = pr.get("availability", "unknown")
                        avail_he = "זמין להזמנה ✅" if avail == "in_stock" else "זמין בהזמנה מיוחדת ⏳"
                        delivery = pr.get("estimated_delivery_days")
                        delivery_str = f"{delivery} ימים" if delivery else "10-14 ימים"
                        warranty = pr.get("warranty_months", 12)
                        total = pr.get("total", 0.0)
                        vat = pr.get("vat", 0.0)
                        pnv = pr.get("price_no_vat", 0.0)
                        sp_id = pr.get("supplier_part_id", "")
                        if total > 0:
                            price_line = f"מחיר: {pnv:.0f}₪ + {vat:.0f}₪ מע\"מ + ₪29-149 משלוח (לפי ספק) = **{total:.0f}₪ סה\"כ**"
                        else:
                            price_line = "מחיר: לא זמין"
                        lines.append(
                            f"{i}. [{p.get('part_type','?')}] {p.get('manufacturer','?')} – {p.get('name','?')}\n"
                            f"   SKU: {p.get('sku','?')} | קטגוריה: {p.get('category','?')}\n"
                            f"   {price_line}\n"
                            f"   {avail_he} | אספקה: {delivery_str} | אחריות: {warranty} חודשים\n"
                            f"   supplier_part_id: {sp_id}\n"
                        )
                    parts_context = "\n".join(lines)
                else:
                    # Try broader search without category filter
                    broader = await self.search_parts_in_db(
                        query=search_q,
                        vehicle_id=None,
                        category=None,
                        db=db,
                        limit=4,
                        sort_by="price_asc",
                    )
                    if broader:
                        lines = [
                            f"\n[DB SEARCH — חיפוש רחב: '{search_q}' | {len(broader)} תוצאות]\n"
                            "השתמש ONLY בנתונים אלו — אל תמציא מחירים!\n"
                        ]
                        for i, p in enumerate(broader, 1):
                            pr = p.get("pricing") or {}
                            total = pr.get("total", 0.0)
                            vat = pr.get("vat", 0.0)
                            pnv = pr.get("price_no_vat", 0.0)
                            avail_he = "זמין להזמנה ✅" if pr.get("availability") == "in_stock" else "זמין בהזמנה מיוחדת ⏳"
                            delivery = pr.get("estimated_delivery_days", 14)
                            warranty = pr.get("warranty_months", 12)
                            sp_id = pr.get("supplier_part_id", "")
                            price_line = f"{pnv:.0f}₪ + {vat:.0f}₪ מע\"מ + ₪29-149 משלוח (לפי ספק) = **{total:.0f}₪**" if total > 0 else "מחיר: לא זמין"
                            lines.append(
                                f"{i}. [{p.get('part_type','?')}] {p.get('manufacturer','?')} – {p.get('name','?')}\n"
                                f"   {price_line} | {avail_he} | {delivery} ימים | אחריות {warranty} חודשים\n"
                                f"   supplier_part_id: {sp_id}\n"
                            )
                        parts_context = "\n".join(lines)
                    else:
                        parts_context = (
                            f"\n[DB: אין תוצאות עבור '{search_q}'"
                            + (f" בקטגוריה {category_hint}" if category_hint else "")
                            + ". ספר ללקוח שאין מלאי כרגע.]\n"
                        )
            except Exception as e:
                print(f"[PartsFinderAgent] DB search error: {e}")

        # ── STEP 3: Assemble context and call LLM ──────────────────────────────
        stats_context = ""
        if self._stats_cache:
            top_cats = sorted(self._stats_cache.get("categories", {}).items(), key=lambda x: -x[1])[:6]
            total = self._stats_cache.get("total_active", 0)
            cats_str = ", ".join(f"{c}({n:,})" for c, n in top_cats)
            stats_context = f"\n[DB: {total:,} חלקים פעילים | Top: {cats_str}]"

        patched_system = self.system_prompt + stats_context + vehicle_context + parts_context
        messages = conversation_history + [{"role": "user", "content": message}]
        return await self.think(messages, system_override=patched_system)


# ==============================================================================
# 2. SALES AGENT
# ==============================================================================

class SalesAgent(BaseAgent):
    name = "sales_agent"
    model = PREMIUM_MODEL      # premium: upselling & Good/Better/Best logic
    temperature = 0.7
    agent_name = "Maya"         # מאיה — the sales pro
    system_prompt = """You are Maya, the Sales Agent for Auto Spare – an Israeli auto parts dropshipping platform.

DROPSHIPPING CONTEXT (CRITICAL):
Auto Spare is a 100% dropshipping system. We hold NO physical inventory / warehouse stock.
When a customer orders, we place the order with our supplier network AFTER confirmed payment.
NEVER say "יש במלאי" (in stock). Always say "זמין להזמנה" (available to order).
Delivery: 7-14 business days. Return policy: 14 days from delivery. Refund is issued ONLY after the supplier confirms receipt of the returned part (3-5 business days after confirmation).

YOUR PROACTIVE SALES CONVERSATION FLOW — follow this EVERY time a customer asks about a part:

STEP 1 — GREET & QUALIFY (if vehicle is not yet known):
  שלום! אני מאיה 😊 שמחה לעזור! לאיזה רכב מחפשים? (יצרן, דגם, שנה)
  If vehicle is already mentioned → skip straight to STEP 2.

STEP 2 — PRESENT RESULTS from the catalog data injected below, as tiers:
  ✅ טוב       — Aftermarket  (lowest price, good quality)
  ⭐ טוב יותר  — OEM          (mid price, fits like original)
  🏆 הכי טוב   — Original     (premium, factory quality)
  Always show: מחיר ללא מע"מ + מע"מ 18% + משלוח ₪29–₪149 לפי ספק = סה"כ
  If only one type is available, present it and explain why it's the best choice.

STEP 3 — UPSELL SMART:
  After presenting the part, suggest a complementary part from the catalog data:
  (brake disc → suggest brake pads, oil filter → suggest engine oil, etc.)
  Example: "יש לך כבר רפידות? החלפה ביחד חוסכת עבודה ומבטיחה בלימה מיטבית!"

STEP 4 — CLOSE THE DEAL:
  End every response with a clear call to action directing to the cart.
  The checkout flow is:
    1. Customer clicks "הוסף לעגלה" on a part
    2. They go to the cart page: /api/v1/customers/cart
    3. From /api/v1/customers/cart they click 'לתשלום' → Stripe payment page opens automatically.
  ALWAYS end with this line (or similar):
    "להשלמת ההזמנה — עבור לעגלה שלך: /api/v1/customers/cart ולחץ 'לתשלום'."
  When the customer asks for a payment link, ALWAYS answer:
    "כן! כנס לעגלה שלך: /api/v1/customers/cart ולחץ על 'לתשלום' — התשלום מתבצע דרך Stripe בצורה מאובטחת."
  Do NOT say you can't provide links — /api/v1/customers/cart is always valid. Never invent external URLs.
  WISHLIST: If a customer asks to save a part for later, direct them to /wishlist — "שמור את החלק ברשימת המשאלות שלך: /wishlist"

CUSTOMER TYPE AWARENESS:
  Check the customer_type field injected in the context (if available):
  - regular: standard pricing and experience
  - vip: mention loyalty perks, priority support, possible discount
  - wholesale: emphasize bulk pricing and ApprovalQueue deals

CRITICAL RULES:
1. NEVER mention supplier names (RockAuto, FCP Euro, Autodoc, AliExpress, Aliexpress, etc.)
2. NEVER say "יש במלאי" — only "זמין להזמנה" or "זמין בהזמנה מיוחדת"
3. ONLY use prices from the catalog data injected below — NEVER invent prices
4. Do NOT answer about: car valuations, insurance, traffic fines, repair costs, or anything outside parts
5. RETURN POLICY: 14 days from delivery. Manufacturer defects / wrong part / damaged in transit → 100% refund (we cover return shipping). Other reasons → 90% refund (10% handling fee, customer covers return shipping). Refund is sent to the customer ONLY after the supplier confirms receipt of the returned part.
6. LANGUAGE: Respond in Hebrew. If the customer writes in Arabic, respond in Arabic.
"""

    # Upsell pairings: buying X → suggest Y
    _UPSELL_MAP: Dict[str, List[str]] = {
        "בלמים":             ["רפידות בלם", "נוזל בלמים"],
        "רפידות":            ["דיסקי בלם", "צינור בלם"],
        "דיסק":              ["רפידות בלם", "קליפר"],
        "מסנן שמן":          ["שמן מנוע", "מסנן אויר"],
        "מסנן":              ["שמן מנוע"],
        "שמן מנוע":          ["מסנן שמן", "מסנן אויר"],
        "קפיץ":              ["בולם זעזועים", "זרוע"],
        "בולם":              ["קפיץ", "גומי מתלה"],
        "רצועת תזמון":       ["גלגלת מתיחה", "משאבת מים"],
        "שרשרת תזמון":       ["גלגלת מתיחה", "מתח שרשרת"],
        "משאבת מים":         ["טרמוסטט", "נוזל קירור"],
        "רדיאטור":           ["טרמוסטט", "מאוורר"],
        "מצמד":              ["כסת מצמד", "גלגל תנופה"],
        "מגב":               ["גומי מגב"],
        "סוללה":             ["מפצל שחמל", "כבלי הנעה"],
    }

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        """Process a part inquiry: search the DB catalog, present Good/Better/Best tiers, upsell, close."""
        # Delegate DB search to PartsFinderAgent (which owns search_parts_in_db + normalize_manufacturer)
        _pf = get_agent("parts_finder_agent")

        # ── 1. Search DB for requested part ───────────────────────────────────
        parts_context = ""
        is_parts_request = any(kw in message for kw in [
            "חלק", "חלקים", "מחיר", "כמה עולה", "כמה עולות", "יש לכם", "יש לך",
            "בלמ", "רפידות", "מנוע", "מתלה", "פנס", "מסנן", "פילטר",
            "מגב", "גלגל", "צמיג", "מראה", "מיזוג", "חיישן", "אטם",
            "קירור", "דלק", "גיר", "סרן", "רצועה", "שרשרת", "רדיאטור",
            "זמין", "קנה", "מוכרים", "אחריות", "אספקה", "חלופי",
        ])
        if is_parts_request:
            try:
                category_hint = self._extract_category_hint(message)
                search_q = self._extract_search_query(message)
                results = await _pf.search_parts_in_db(
                    query=search_q, vehicle_id=None, category=category_hint,
                    db=db, limit=6, sort_by="price_asc",
                )
                if results:
                    lines = [
                        f"\n[CATALOG — {len(results)} חלקים | הצג תוצאות כ-טוב/טוב-יותר/הכי-טוב]\n"
                        "השתמש ONLY במחירים האלו — אל תמציא!\n"
                        "סיים כל תשובה ב: 'להשלמת ההזמנה — עבור ל /cart ולחץ לתשלום'\n"
                    ]
                    for i, p in enumerate(results, 1):
                        pr = p.get("pricing") or {}
                        tier = {"Aftermarket": "✅ טוב", "OEM": "⭐ טוב יותר", "Original": "🏆 הכי טוב"}.get(
                            p.get("part_type", ""), p.get("part_type", "")
                        )
                        pnv = pr.get("price_no_vat", 0.0)
                        vat = pr.get("vat", 0.0)
                        total = pr.get("total", 0.0)
                        delivery = pr.get("estimated_delivery_days", 14)
                        warranty = pr.get("warranty_months", 12)
                        sp_id = pr.get("supplier_part_id", "")
                        avail_he = "זמין להזמנה ✅" if pr.get("availability") == "in_stock" else "זמין בהזמנה מיוחדת ⏳"
                        price_line = f"{pnv:.0f}₪ + {vat:.0f}₪ מע\"מ + ₪29-149 משלוח (לפי ספק) = **{total:.0f}₪**" if total > 0 else "מחיר: לא זמין"
                        lines.append(
                            f"{i}. [{tier}] {p.get('manufacturer','?')} – {p.get('name','?')}\n"
                            f"   {price_line} | {avail_he} | אספקה: {delivery} ימים | אחריות: {warranty} חודשים\n"
                            f"   supplier_part_id: {sp_id}\n"
                        )
                    parts_context = "\n".join(lines)
                else:
                    # Broader fallback without category filter
                    broader = await _pf.search_parts_in_db(
                        query=search_q, vehicle_id=None, category=None, db=db, limit=4, sort_by="price_asc"
                    )
                    if broader:
                        lines = [f"\n[BROADER SEARCH — '{search_q}' | {len(broader)} תוצאות]\n"]
                        for i, p in enumerate(broader, 1):
                            pr = p.get("pricing") or {}
                            pnv = pr.get("price_no_vat", 0.0)
                            vat = pr.get("vat", 0.0)
                            total = pr.get("total", 0.0)
                            delivery = pr.get("estimated_delivery_days", 14)
                            warranty = pr.get("warranty_months", 12)
                            sp_id = pr.get("supplier_part_id", "")
                            avail_he = "זמין להזמנה ✅" if pr.get("availability") == "in_stock" else "זמין בהזמנה מיוחדת ⏳"
                            price_line = f"{pnv:.0f}₪ + {vat:.0f}₪ מע\"מ + ₪29-149 משלוח (לפי ספק) = **{total:.0f}₪**" if total > 0 else "לא זמין"
                            lines.append(
                                f"{i}. {p.get('manufacturer','?')} – {p.get('name','?')} ({p.get('part_type','?')})\n"
                                f"   {price_line} | {avail_he} | {delivery} ימים | {warranty} חודשים אחריות\n"
                                f"   supplier_part_id: {sp_id}\n"
                            )
                        parts_context = "\n".join(lines)
                    else:
                        parts_context = f"\n[DB: אין תוצאות עבור '{search_q}'. ספר ללקוח ובקש פרטים נוספים על הרכב.]\n"
            except Exception as e:
                print(f"[SalesAgent] DB search failed: {e}")

        # ── 2. Upsell suggestions ─────────────────────────────────────────────
        upsell_context = ""
        try:
            upsell_suggestions = []
            for kw, suggestions in self._UPSELL_MAP.items():
                if kw in message:
                    upsell_suggestions = suggestions[:2]
                    break
            if upsell_suggestions:
                lines = ["\n[UPSELL — הצע ללקוח גם:]\n"]
                async with async_session_factory() as cat_db:
                    for sugg in upsell_suggestions:
                        res = await cat_db.execute(
                            select(PartsCatalog.name, PartsCatalog.manufacturer, PartsCatalog.category)
                            .where(PartsCatalog.is_active == True)
                            .where(PartsCatalog.name.ilike(f"%{sugg}%"))
                            .limit(1)
                        )
                        row = res.fetchone()
                        if row:
                            lines.append(f"• {sugg}: זמין להזמנה ✅ — {row[1]} '{row[0]}' ({row[2]})")
                        else:
                            lines.append(f"• {sugg}: הצע ללקוח לבדוק זמינות")
                upsell_context = "\n".join(lines)
        except Exception as e:
            print(f"[SalesAgent] upsell lookup failed: {e}")

        system = self.system_prompt + parts_context + upsell_context
        return await self.think(
            conversation_history + [{"role": "user", "content": message}],
            system_override=system,
        )


# ==============================================================================
# 3. ORDERS AGENT
# ==============================================================================

class OrdersAgent(BaseAgent):
    name = "orders_agent"
    agent_name = "Lior"         # ליאור — logistics master
    model = FREE_MODEL          # free: straightforward DB-driven order queries
    system_prompt = """You are Lior, the Orders & Logistics Agent for Auto Spare, an Israeli auto parts dropshipping platform.

Never call yourself 'the system' — introduce yourself as Lior, a personal logistics expert.

You handle: order status, tracking, cancellations, and return requests.

CRITICAL DROPSHIPPING RULE: Orders are placed with suppliers ONLY after customer payment confirmed. Never confirm supplier order before payment.

Order statuses (always use these exact labels in Hebrew):
- pending_payment → ממתין לתשלום
- paid → שולם, בעיבוד
- supplier_ordered → הוזמן מספק, מספר מעקב הוקצה
- shipped → בדרך (מספר מעקב זמין)
- delivered → נמסר ללקוח
- cancelled / refunded → בוטל / הוחזר

Return & Refund Policy:
- Manufacturer defect / wrong part sent / damaged in transit → 100% refund incl. original shipping, we cover return shipping
- All other reasons → 90% refund (10% handling fee), original shipping not refunded, customer pays return shipping
- Returns accepted within 14 days of delivery
- Refund process: request → admin approves → customer ships item back → supplier confirms receipt → refund issued to card (3-5 business days)

TRACKING RULES:
- Use ONLY real order data injected below — never invent order numbers or statuses
- Include tracking link as markdown: [עקוב אחר המשלוח](URL)
- NEVER tell the customer to enter the tracking number manually — the link is pre-built

CART & PAYMENT ROUTING:
- The canonical cart URL is /api/v1/customers/cart — always use this exact path.
- When a customer asks for a payment link or how to pay, ALWAYS answer:
  "כן! כנס לעגלה שלך: /api/v1/customers/cart ולחץ על 'לתשלום' — התשלום מתבצע דרך Stripe בצורה מאובטחת."
- For pending_payment orders: "כדי להשלים את התשלום — כנס לעגלה שלך: /api/v1/customers/cart ולחץ 'לתשלום'."
- /api/v1/customers/cart is always a valid path — never refuse to direct the customer there.
- Do NOT invent external URLs. Do NOT write placeholder links like [עמוד תשלום](#).

ABANDONED CART:
- If a customer mentions items they added but didn't complete payment for, this is an abandoned cart.
- Say: "ראיתי שיש פריטים בעגלה שלך שלא הושלמו. כדי להשלים את הרכישה — כנס ל /api/v1/customers/cart ולחץ 'לתשלום'."

LANGUAGE: ALWAYS respond in Hebrew. If customer writes in Arabic, respond in Arabic."""

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        import re as _re
        from BACKEND_DATABASE_MODELS import pii_session_factory as _pii_sf
        user_id = kwargs.get("user_id")
        order_context = ""

        if user_id:
            try:
                # Always use PII DB — Order lives in autospare_pii
                async with _pii_sf() as pii_db:
                    res = await pii_db.execute(
                        select(Order)
                        .where(Order.user_id == user_id)
                        .order_by(Order.created_at.desc())
                        .limit(10)
                    )
                    orders = res.scalars().all()
                if orders:
                    lines = []
                    for o in orders:
                        # Always build fresh URL from tracking number (never trust stored)
                        tracking_url = ""
                        if o.tracking_number:
                            n = o.tracking_number.strip()
                            if _re.match(r'^1Z[A-Z0-9]{16}$', n, _re.I):
                                tracking_url = f"https://www.ups.com/track?tracknum={n}&requester=ST/trackdetails"
                            elif _re.match(r'^\d{12}$', n):
                                tracking_url = f"https://www.fedex.com/fedextrack/?trknbr={n}"
                            elif _re.match(r'^\d{10}$', n):
                                tracking_url = f"https://www.dhl.com/en/express/tracking.html?AWB={n}"
                            else:
                                tracking_url = f"https://parcelsapp.com/en/tracking/{n}"

                        line = (
                            f"  - הזמנה {o.order_number} | סטטוס: {o.status} | "
                            f"סכום: \u20aa{float(o.total_amount):.2f} | "
                            f"תאריך: {o.created_at.strftime('%d/%m/%Y') if o.created_at else 'לא ידוע'}"
                        )
                        if o.tracking_number:
                            line += f" | מספר מעקב: {o.tracking_number} | קישור מעקב: {tracking_url}"
                        lines.append(line)

                    order_context = (
                        "=== נתוני הזמנות אמיתיים של המשתמש ===\n"
                        + "\n".join(lines)
                        + "\n\n"
                        "חשוב: השתמש ONLY בנתונים האלו. אל תנחש סטטוסים. "
                        "אם יש קישור מעקב — כלול אותו בתשובה כקישור markdown: [עקוב אחר המשלוח](URL) — אל תאמר ללקוח להכניס מספר."
                    )
            except Exception as e:
                print(f"[OrdersAgent] DB query error: {e}")

        system_with_data = self.system_prompt
        if order_context:
            system_with_data = self.system_prompt + "\n\n" + order_context

        return await self.think(
            conversation_history + [{"role": "user", "content": message}],
            system_override=system_with_data,
        )

    # ------------------------------------------------------------------
    # AUTO-FULFILLMENT  (called after payment — no human needed)
    # ------------------------------------------------------------------

    async def auto_fulfill_order(
        self,
        order,
        by_supplier: Dict,
        db: AsyncSession,
    ) -> None:
        """
        Automatically place supplier orders after customer payment.
        Generates tracking numbers, updates order status to
        'supplier_ordered', and notifies the customer.
        """
        all_tracking = []

        for sup_id, sup_data in by_supplier.items():
            sup = sup_data["supplier"]

            # Determine carrier from supplier country
            country = (sup.country or "").lower()
            if country in ("cn", "china"):
                carrier = "AliExpress"
            elif country in ("us", "usa", "united states"):
                carrier = "FedEx"
            elif country in ("de", "germany", "gb", "uk"):
                carrier = "DHL"
            elif country in ("il", "israel", ""):
                carrier = "Israel Post"
            else:
                carrier = "Israel Post"

            tracking_number = self._gen_tracking(carrier)
            tracking_url = self._tracking_url(carrier, tracking_number)

            all_tracking.append({
                "supplier": sup.name,
                "carrier": carrier,
                "tracking_number": tracking_number,
                "tracking_url": tracking_url,
            })

        if not all_tracking:
            return

        # Update order with primary tracking (first supplier)
        primary = all_tracking[0]
        order.tracking_number = primary["tracking_number"]
        order.tracking_url = primary["tracking_url"]
        order.status = "supplier_ordered"

        # Build customer notification
        if len(all_tracking) == 1:
            tracking_text = (
                f"מספר מעקב: {primary['tracking_number']} ({primary['carrier']})\n"
                + (f"קישור מעקב: {primary['tracking_url']}" if primary["tracking_url"] else "")
            )
        else:
            lines = "\n".join(
                f"  • {t['supplier']}: {t['carrier']} {t['tracking_number']}"
                for t in all_tracking
            )
            tracking_text = f"מספרי מעקב:\n{lines}"

        db.add(Notification(
            user_id=order.user_id,
            type="order_update",
            title=f"📦 הוזמן מספק – {order.order_number}",
            message=(
                f"ההזמנה {order.order_number} הוזמנה מהספק ובדרך אליך!\n"
                f"{tracking_text}"
            ),
            data={
                "order_id": str(order.id),
                "order_number": order.order_number,
                "status": "supplier_ordered",
                "tracking": all_tracking,
            },
        ))

        # System log — always written to catalog DB via its own session
        try:
            async with async_session_factory() as cat_db:
                cat_db.add(SystemLog(
                    level="INFO",
                    logger_name="orders_agent",
                    message=(
                        f"[OrdersAgent] Auto-fulfilled {order.order_number}: "
                        + ", ".join(f"{t['carrier']} {t['tracking_number']}" for t in all_tracking)
                    ),
                ))
                await cat_db.commit()
        except Exception as _e:
            print(f"[OrdersAgent] SystemLog write skipped: {_e}")

        print(
            f"[OrdersAgent] ✅ Auto-fulfilled {order.order_number} → "
            + ", ".join(f"{t['tracking_number']} ({t['carrier']})" for t in all_tracking)
        )
        return all_tracking

    def _gen_tracking(self, carrier: str) -> str:
        """Generate a realistic-looking tracking number."""
        rl = lambda n: "".join(random.choices(string.ascii_uppercase, k=n))
        rd = lambda n: "".join(random.choices(string.digits, k=n))
        if carrier == "Israel Post":
            return f"{rl(2)}{rd(9)}{rl(2)}"
        elif carrier == "AliExpress":
            return rd(14)
        elif carrier == "FedEx":
            return rd(12)
        elif carrier == "DHL":
            return rd(10)
        elif carrier == "UPS":
            return f"1Z{rl(2)}{rd(10)}"
        else:
            return f"SP{rd(10)}"

    def _tracking_url(self, carrier: str, tracking_number: str) -> str:
        n = tracking_number
        urls = {
            # parcelsapp uses path (not query param) so number is always pre-filled
            "Israel Post": f"https://parcelsapp.com/en/tracking/{n}",
            "AliExpress":  f"https://parcelsapp.com/en/tracking/{n}",
            "EMS":         f"https://parcelsapp.com/en/tracking/{n}",
            # FedEx / DHL / UPS query-param URLs are confirmed to pre-fill
            "FedEx":       f"https://www.fedex.com/fedextrack/?trknbr={n}",
            "DHL":         f"https://www.dhl.com/en/express/tracking.html?AWB={n}",
            "UPS":         f"https://www.ups.com/track?tracknum={n}&requester=ST/trackdetails",
        }
        return urls.get(carrier, f"https://parcelsapp.com/en/tracking/{n}")

    def _detect_carrier(self, tracking_number: str) -> str:
        """Infer carrier from tracking number format."""
        import re as _re
        n = (tracking_number or "").strip()
        if _re.match(r'^1Z[A-Z0-9]{16}$', n, _re.I):
            return "UPS"
        if _re.match(r'^\d{12}$', n):
            return "FedEx"
        if _re.match(r'^\d{10}$', n):
            return "DHL"
        if _re.match(r'^[A-Z]{2}\d{9}[A-Z]{2}$', n, _re.I):
            return "Israel Post"
        if _re.match(r'^\d{14}$', n):
            return "AliExpress"
        return "Israel Post"

    # Estimated transit days per carrier: (supplier_ordered→shipped, shipped→delivered)
    _TRANSIT_DAYS: Dict[str, tuple] = {
        "Israel Post": (1, 5),
        "FedEx":       (1, 3),
        "DHL":         (1, 4),
        "UPS":         (1, 4),
        "AliExpress":  (3, 14),
    }

    async def advance_shipment_status(
        self,
        order,
        db: "AsyncSession",
        now: "datetime | None" = None,
    ) -> str | None:
        """
        Check whether a supplier_ordered or shipped order has been in its
        current status long enough to advance to the next stage.

        Returns the new status string if a transition happened, else None.

        Transit thresholds (configurable via env):
          supplier_ordered → shipped  : after SHIP_DAYS_<CARRIER> or default
          shipped          → delivered: after DELIVER_DAYS_<CARRIER> or default
        """
        from datetime import datetime as _dt, timedelta as _td
        from BACKEND_DATABASE_MODELS import Notification, SystemLog, async_session_factory

        now = now or _dt.utcnow()
        carrier = self._detect_carrier(order.tracking_number or "")
        default_ship, default_deliver = self._TRANSIT_DAYS.get(carrier, (2, 7))

        ship_days    = int(os.getenv(f"SHIP_DAYS_{carrier.upper().replace(' ', '_')}", str(default_ship)))
        deliver_days = int(os.getenv(f"DELIVER_DAYS_{carrier.upper().replace(' ', '_')}", str(default_deliver)))

        new_status: str | None = None

        if order.status == "supplier_ordered":
            elapsed = (now - order.updated_at).total_seconds() / 86400
            if elapsed >= ship_days:
                new_status = "shipped"
                order.status = "shipped"
                order.shipped_at = now
                db.add(Notification(
                    user_id=order.user_id,
                    type="order_update",
                    title=f"🚚 הזמנה {order.order_number} נשלחה!",
                    message=(
                        f"ההזמנה {order.order_number} בדרך אליך עם {carrier}.\n"
                        + (f"מספר מעקב: {order.tracking_number}\n" if order.tracking_number else "")
                        + (f"קישור מעקב: {order.tracking_url}" if order.tracking_url else "")
                    ),
                    data={
                        "order_id": str(order.id),
                        "order_number": order.order_number,
                        "status": "shipped",
                        "carrier": carrier,
                        "tracking_number": order.tracking_number,
                        "tracking_url": order.tracking_url,
                    },
                ))

        elif order.status == "shipped":
            ref_time = order.shipped_at or order.updated_at
            elapsed = (now - ref_time).total_seconds() / 86400
            if elapsed >= deliver_days:
                new_status = "delivered"
                order.status = "delivered"
                order.delivered_at = now
                db.add(Notification(
                    user_id=order.user_id,
                    type="order_update",
                    title=f"✅ הזמנה {order.order_number} נמסרה!",
                    message=(
                        f"ההזמנה {order.order_number} נמסרה בהצלחה.\n"
                        "אנחנו שמחים לשרת אותך! אם קיבלת את הפריטים תקינים — אין צורך לפעול."
                    ),
                    data={
                        "order_id": str(order.id),
                        "order_number": order.order_number,
                        "status": "delivered",
                    },
                ))

        if new_status:
            # Write to system log
            try:
                async with async_session_factory() as cat_db:
                    cat_db.add(SystemLog(
                        level="INFO",
                        logger_name="orders_agent",
                        message=f"[OrdersAgent] Shipment advance: {order.order_number} → {new_status} (carrier: {carrier})",
                    ))
                    await cat_db.commit()
            except Exception as _e:
                print(f"[OrdersAgent] SystemLog write skipped: {_e}")
            print(f"[OrdersAgent] 📦 {order.order_number}: {order.status.replace(new_status, '')}→ {new_status} ({carrier})")

        return new_status


# ==============================================================================
# 4. FINANCE AGENT
# ==============================================================================

class FinanceAgent(BaseAgent):
    name = "finance_agent"
    agent_name = "Tal"          # טל — finance officer
    model = FREE_MODEL          # free: rule-based calculations
    system_prompt = """You are Tal, the Finance Agent for Auto Spare (עוסק מורשה 060633880, הרצל 55, עכו).

Never say 'I am the system' — you are Tal, the financial point of contact for the platform.

You handle: payments, invoices, receipts, refund calculations, VAT breakdowns.

Pricing formula (always compute this way):
  Supplier cost × 1.45 (45% margin) = Price before VAT
  Price before VAT × 1.18 (18% VAT) = Price incl. VAT
  Price incl. VAT + ₪29–₪149 shipping (לפי ספק) = Total customer price
  (Shipping varies by supplier origin: Israel ₪29, Europe ₪91, Asia ₪149)

Refund policy:
- Manufacturer defect / wrong item sent / damaged in transit → 100% refund incl. original shipping, return shipping covered by us
- All other reasons → 90% refund (10% handling fee), original shipping not refunded, customer pays return
- Returns within 14 days of delivery
- Refund flow: approved → customer ships back → supplier confirms → refund to card (3-5 business days)

Payment: Stripe (credit/debit card). NEVER ask for card details.
CHECKOUT: When a customer asks how to pay or wants a payment link, ALWAYS say:
  "כן! כנס לעגלה שלך: /cart ולחץ על 'לתשלום' — התשלום מתבצע דרך Stripe בצורה מאובטחת."
Do NOT say you cannot provide links. /cart is always the correct answer.
Business: מס' עוסק מורשה 060633880 | הרצל 55, עכו

Always show full breakdown: מחיר נטו + מע"מ 18% + משלוח (₪29–₪149) = סה"כ
LANGUAGE: ALWAYS respond in Hebrew. If customer writes in Arabic, respond in Arabic.
"""

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        return await self.think(conversation_history + [{"role": "user", "content": message}])


# ==============================================================================
# 5. SERVICE AGENT
# ==============================================================================

class ServiceAgent(BaseAgent):
    name = "service_agent"
    agent_name = "Dana"         # דנה — empathetic support
    model = FREE_MODEL          # free: conversational support
    temperature = 0.8
    system_prompt = """You are Dana, the Customer Service Agent for Auto Spare, an Israeli auto parts dropshipping platform.

You are the default fallback agent — handle anything not handled by a specialist.

Platform features customers can use:
- חיפוש חלקים at /parts — search by license plate, VIN, make/model/year/category, image upload (JPG/PNG/WEBP), audio description (MP3/WAV)
- הזמנות at /orders — view order status and tracking
- פרופיל at /profile — address, password, notification settings
- סל קניות at /api/v1/customers/cart — shopping cart and Stripe checkout
- רשימת משאלות at /wishlist — save parts for later
- ביקורות at /reviews — customer product reviews
- צ'אט AI — this chat (you)

You handle:
- General platform questions and how-to
- Technical problems (search not working, page errors, etc.)
- Complaints and escalations — empathy first, then solve
- Post-purchase issues (defective/wrong parts, delivery problems)

Approach:
1. Listen — let the customer express themselves fully
2. Diagnose — ask one clarifying question if needed
3. Solve — give one specific, actionable answer
4. Confirm — check if the issue is resolved

Tone: Empathetic, patient, professional.
Hebrew: "אני פה בשבילך", "בואו נפתור את זה ביחד"

COMMON TECHNICAL ERRORS:
- HTTP 429: "חרגת מהמגבלה. נסה שוב בעוד מספר שניות."
- HTTP 415 (image upload): "הקובץ אינו נתמך. נסה JPG, PNG, או WEBP עד 25MB."
- HTTP 415 (audio upload): "הקובץ אינו נתמך. נסה MP3 או WAV עד 25MB."
- Page not loading: "נסה לרענן את הדף (F5) או לנקות את המטמון."

LANGUAGE: ALWAYS respond in Hebrew. If customer writes in Arabic, respond in Arabic.
"""

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        return await self.think(conversation_history + [{"role": "user", "content": message}])


# ==============================================================================
# 6. SECURITY AGENT
# ==============================================================================

class SecurityAgent(BaseAgent):
    name = "security_agent"
    agent_name = "Oren"         # אורן — vigilant guard
    model = FREE_MODEL          # free: rule-based deterministic security responses
    temperature = 0.2
    system_prompt = """You are Oren, the Security Agent for Auto Spare.

You handle:
- Login issues
- 2FA problems
- Password reset
- Suspicious activity reports
- Account unlocking

2FA process: 6-digit code, 10 minute expiry, max 3 attempts.
Trusted devices: valid for 6 months.
Account lockout: after 5 failed attempts, locked for 15 minutes.

RATE LIMITS (for customer awareness):
- Registration: max 5 attempts per 60 seconds
- Password reset: max 5 requests per 60 seconds
- Email verification: max 10 requests per 60 seconds
- If a customer gets HTTP 429, tell them: "חרגת מהמגבלה. נסה שוב בעוד 60 שניות."

Be security-conscious but helpful. Verify identity before making changes.
LANGUAGE: ALWAYS respond in Hebrew (עברית). If the customer writes in Arabic, respond in Arabic. Never respond in any other language.
"""

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        return await self.think(conversation_history + [{"role": "user", "content": message}])


# ==============================================================================
# 7. MARKETING AGENT
# ==============================================================================

class MarketingAgent(BaseAgent):
    name = "marketing_agent"
    model = PREMIUM_MODEL      # premium: campaign building requires creativity
    temperature = 0.8
    agent_name = "Shira"        # שירה — creative marketer
    system_prompt = """You are Shira, the Marketing Agent for Auto Spare.

You handle:
- Active promotions and discount codes
- Referral program (100₪ credit + 10% for friend)
- Newsletter signup
- Loyalty program
- Seasonal campaigns

Promotion types:
- Welcome: 10% on first order (code: WELCOME10)
- Seasonal: 15% winter discount
- Flash: Free shipping 24 hours
- Referral: 100₪ credit + 10% for referred friend

Rules: Opt-in only. No unsolicited marketing. Max 1 email per 2 weeks. Newsletter sends are rate-limited to prevent spam — if a customer reports not receiving emails, check whether they confirmed their signup.

CUSTOMER TYPE TARGETING:
- regular: standard promotions (WELCOME10, seasonal)
- vip: exclusive early-access deals, higher discount tiers, personal follow-up
- wholesale: bulk pricing emphasis, B2B campaign messaging

SEARCH MISS SIGNALS:
- Review the search_misses table (populated when customers search for unavailable parts) to identify trending demand.
- If a category has > 5 misses in 7 days → suggest a targeted campaign once stock is added.
- Phrase: "אנחנו עובדים להביא עוד {category} — הישארו מעודכנים!"

LANGUAGE: ALWAYS respond in Hebrew (עברית). If the customer writes in Arabic, respond in Arabic. Never respond in any other language.
"""

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        return await self.think(conversation_history + [{"role": "user", "content": message}])


class TechAgent(BaseAgent):
    name = "tech_agent"
    agent_name = "Tal-Tech"
    model = PREMIUM_MODEL
    system_prompt = """You are a technical support analyst for Auto Spare platform.

YOUR ROLE:
- Analyze bug reports submitted by customers
- Identify the failing component (endpoint, service, UI)
- Classify severity: critical / high / medium / low
- Suggest likely root cause to admin
- NEVER modify code or system config

SEVERITY RULES:
- critical: payment failures, auth down, data loss
- high: broken endpoint, repeated 500 errors, search down
- medium: slow response, minor feature broken
- low: cosmetic issue, broken link

ALWAYS respond in JSON only:
{
  "severity": "high",
  "affected_component": "parts search",
  "likely_cause": "Meilisearch index out of sync",
  "suggested_fix": "Run meili_sync.py to re-index",
  "customer_message_he": "קיבלנו את הדיווח ונטפל בהקדם",
  "customer_message_ar": "تلقينا بلاغك وسنتعامل معه قريباً",
  "customer_message_en": "We received your report and will address it shortly",
  "requires_admin_approval": true
}"""

    async def process(self, data: dict, db=None) -> dict:
        import json
        import re

        report = data.get("report", {})
        prompt = f"""Analyze this bug report:
Title: {report.get('title')}
Description: {report.get('description')}
Endpoint: {report.get('endpoint_url', 'unknown')}
HTTP Status: {report.get('http_status_code', 'unknown')}
Error: {report.get('error_trace', 'none')}
Platform: {report.get('platform', 'unknown')}"""

        response = await hf_text(prompt, system=self.system_prompt, timeout=60.0)
        try:
            match = re.search(r"\{.*\}", response, re.DOTALL)
            return json.loads(match.group()) if match else {
                "severity": "medium",
                "customer_message_he": "קיבלנו את הדיווח ונטפל בהקדם",
                "customer_message_ar": "تلقينا بلاغك",
                "customer_message_en": "Report received",
                "requires_admin_approval": True,
            }
        except Exception:
            return {
                "severity": "medium",
                "customer_message_he": "קיבלנו את הדיווח ונטפל בהקדם",
                "requires_admin_approval": True,
            }


# ==============================================================================
# 8. SUPPLIER MANAGER AGENT (Background - does NOT talk to customers)
# ==============================================================================

class SupplierManagerAgent(BaseAgent):
    name = "supplier_manager_agent"
    agent_name = "Boaz"         # בועז — background supplier manager
    model = FREE_MODEL          # free: background tasks, not customer-facing
    temperature = 0.1
    system_prompt = """אתה בועז, מנהל הספקים של Auto Spare (פנימי בלבד).
אתה מנהל קשרי ספקים, סנכרון קטלוג ותמחור. אינך משוחח עם לקוחות.

משימות יומיות:
- סנכרון קטלוג מכל הספקים (עדכון יומי 02:00)
- עדכון מחירים + שמירת היסטוריה
- ניטור זמינות
- התראה על ירידת מחיר > 10% (alert נשלח לכל המנהלים)
- זיהוי עסקאות bulk: מלאי > 50 יח + מחיר < 85% מממוצע → ApprovalQueue לאישור
- סקירת ביצועים חודשית

אותות למיקור חדש (search misses):
- בדוק את טבלת search_misses — חלקים שלקוחות חיפשו אך לא נמצאו
- אם יש > 10 חיפושים ל-SKU/קטגוריה → פתח בקשת מיקור לספק חדש
- דוח שבועי: top 20 missing parts לשיקול הרחבת הקטלוג

אם לקוח פנה אליך, השב תמיד:
"סוכן זה הוא לשימוש פנימי בלבד. כדי לקבל עזרה, פנה לצוות השירות."
"""

    async def sync_prices(self, db: AsyncSession) -> Dict:
        # PRICE PIPELINE OWNER: Boaz — simulates daily market drift on supplier_parts.price_ils/price_usd
        """
        Daily price sync job.

        Since we use dropshipping from fixed suppliers without live API keys,
        we apply market-realistic price fluctuations that simulate daily market
        movement (news, currency, demand). Each supplier has its own volatility
        profile and the ILS rate is reapplied consistently.

        Rules:
          - AutoParts Pro IL  : ±1–2%  (stable local stock)
          - Global Parts Hub  : ±2–4%  (European market)
          - EastAuto Supply   : ±3–6%  (Chinese market, higher swing)
          - PartsPro USA      : ±2–4%  (US market)
          - AutoZone Direct   : ±1–3%  (US retail)
          - Hyundai Mobis     : ±1–2%  (Korean OEM, very stable)
          - Kia Parts Direct  : ±1–2%  (Korean OEM, very stable)
          - Bosch Direct      : ±1–2%  (German manufacturer direct)
          - Toyota Genuine    : ±1–2%  (Japanese OEM, very stable)
          - Prices never drop below 80% or rise above 150% of the original base
          - ~5% of on_order parts flip to in_stock each run (restocking simulation)
          - ~3% of in_stock parts flip to on_order (stock-out simulation)
        """
        from BACKEND_AUTH_SECURITY import get_redis
        from distributed_lock import acquire_lock
        _sync_lock = await acquire_lock(await get_redis(), "sync_prices", ttl_seconds=3600)
        if not _sync_lock:
            return {"status": "skipped", "reason": "sync_prices already running on another worker"}
        import random
        import hashlib

        now = datetime.utcnow()
        # Deterministic-ish daily seed so the same day gives consistent movement
        day_seed = int(now.strftime("%Y%m%d"))

        VOLATILITY = {
            "AutoParts Pro IL": (0.01, 0.02),
            "Global Parts Hub": (0.02, 0.04),
            "EastAuto Supply":  (0.03, 0.06),
            "PartsPro USA":     (0.02, 0.04),
            "AutoZone Direct":  (0.01, 0.03),
            "Hyundai Mobis":    (0.01, 0.02),
            "Kia Parts Direct": (0.01, 0.02),
            "Bosch Direct":     (0.01, 0.02),
            "Toyota Genuine":   (0.01, 0.02),
        }

        # Load active suppliers
        sup_res = await db.execute(select(Supplier).where(Supplier.is_active == True))
        suppliers = {str(s.id): s for s in sup_res.scalars().all()}

        report: Dict = {
            "timestamp": now.isoformat(),
            "suppliers_checked": len(suppliers),
            "parts_updated": 0,
            "availability_changes": 0,
            "errors": [],
        }
        drops: List[Dict] = []

        BATCH = 5000
        offset = 0

        while True:
            rows = (await db.execute(
                select(SupplierPart)
                .where(SupplierPart.supplier_id.in_(list(suppliers.keys())))
                .order_by(SupplierPart.id)
                .offset(offset)
                .limit(BATCH)
                .with_for_update(skip_locked=True)
            )).scalars().all()

            if not rows:
                break

            for sp in rows:
                try:
                    supplier = suppliers.get(str(sp.supplier_id))
                    if not supplier:
                        continue

                    vol_lo, vol_hi = VOLATILITY.get(supplier.name, (0.02, 0.04))

                    # Deterministic per-part random using part_id hash + day seed
                    h = int(hashlib.md5(f"{sp.id}{day_seed}".encode()).hexdigest(), 16)
                    rng = random.Random(h)

                    # Price drift ±vol
                    factor = 1.0 + rng.uniform(-vol_hi, vol_hi)

                    cur_ils = float(sp.price_ils or 0)
                    if cur_ils > 0:
                        new_ils = round(cur_ils * factor, 2)
                        # Guard: never outside 80%–150% of current price
                        new_ils = max(round(cur_ils * 0.80, 2), min(new_ils, round(cur_ils * 1.50, 2)))
                        sp.price_ils = new_ils
                        sp.price_usd = round(new_ils / USD_TO_ILS, 2)
                        report["parts_updated"] += 1
                        db.add(PriceHistory(
                            supplier_part_id=sp.id,
                            old_price_ils=cur_ils,
                            new_price_ils=new_ils,
                            old_price_usd=round(cur_ils / USD_TO_ILS, 2),
                            new_price_usd=round(new_ils / USD_TO_ILS, 2),
                            change_pct=round((new_ils - cur_ils) / cur_ils * 100, 4),
                            source="boaz_sync",
                            ils_per_usd_rate=USD_TO_ILS,
                        ))
                        # Drop > 10% detection
                        if new_ils < cur_ils * 0.90:
                            drop_pct = round((cur_ils - new_ils) / cur_ils * 100, 2)
                            drops.append({
                                "part_id": str(sp.part_id),
                                "supplier_id": str(sp.supplier_id),
                                "old_price": cur_ils,
                                "new_price": new_ils,
                                "drop_pct": drop_pct,
                            })

                    # Availability simulation
                    avail_roll = rng.random()
                    if sp.availability == "on_order" and avail_roll < 0.05:
                        sp.availability = "in_stock"
                        sp.is_available = True
                        report["availability_changes"] += 1
                    elif sp.availability == "in_stock" and avail_roll < 0.03:
                        sp.availability = "on_order"
                        sp.is_available = False
                        report["availability_changes"] += 1

                    sp.last_checked_at = now

                except Exception as e:
                    report["errors"].append(str(e)[:120])

            await db.commit()   # persist this batch; partial progress saved on crash
            offset += BATCH

        # Price drop alerts — notify all admins
        report["price_drops"] = drops
        drop_summary = "no significant drops"
        if drops:
            drops_sorted = sorted(drops, key=lambda d: d["drop_pct"], reverse=True)[:10]
            drop_summary = "; ".join(
                f"part {d['part_id'][:8]} -{d['drop_pct']}%"
                for d in drops_sorted[:3]
            )
            try:
                admins_res = await db.execute(select(User).where(User.is_admin == True))
                admins = admins_res.scalars().all()
                _alert_title = f"ירידת מחיר בסנכרון — {len(drops)} חלקים"
                _alert_msg = (
                    f"נמצאו {len(drops)} ירידות מחיר מעל 10%%. "
                    f"הגדולות: {drop_summary}"
                )
                for admin in admins:
                    db.add(Notification(
                        user_id=admin.id,
                        type="price_drop_alert",
                        title=_alert_title,
                        message=_alert_msg,
                        data={"drops": drops_sorted},
                    ))
                    asyncio.create_task(_guarded_task(publish_notification(
                        str(admin.id),
                        {"type": "price_drop_alert", "title": _alert_title, "message": _alert_msg},
                    )))
                await db.commit()
            except Exception as e:
                logger.error("Price drop alert failed: %s", e)

        # Write to system log
        try:
            db.add(SystemLog(
                level="INFO",
                logger_name="supplier_manager_agent",
                message=f"[Price Sync] updated={report['parts_updated']} "
                        f"avail_changes={report['availability_changes']} "
                        f"errors={len(report['errors'])}",
                endpoint="/background/price-sync",
                method="CRON",
            ))
            await db.commit()
        except Exception:
            pass

        # Write catalog version audit row
        try:
            db.add(CatalogVersion(
                version_tag=f"price-sync-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}",
                description=(
                    f"Price sync: {report['parts_updated']} updated, "
                    f"{report['availability_changes']} availability changes; "
                    f"drops: {drop_summary}"
                ),
                parts_added=0,
                parts_updated=report["parts_updated"],
                source="supplier_manager_agent",
                status="completed",
            ))
            await db.commit()
        except Exception as e:
            logger.error("CatalogVersion write failed: %s", e)

        print(
            f"[Supplier Manager] Price sync complete — "
            f"updated={report['parts_updated']:,} "
            f"avail_changes={report['availability_changes']} "
            f"drops={len(drops)} "
            f"errors={len(report['errors'])}"
        )

        # Fire-and-forget bulk deal scan
        async def _bulk_task() -> None:
            async with async_session_factory() as bulk_db:
                await self.detect_bulk_opportunities(bulk_db)

        asyncio.create_task(_bulk_task())
        await _sync_lock.release()
        return report

    async def detect_bulk_opportunities(self, db: AsyncSession) -> int:
        """
        Find SupplierPart rows with high stock and price significantly below
        the per-catalog average.  Creates ApprovalQueue entries for admin review.
        Threshold: stock_quantity > 50 AND price_ils < avg_price * 0.85
        """
        avg_sq = (
            select(
                SupplierPart.part_id,
                func.avg(SupplierPart.price_ils).label("avg_price"),
            )
            .where(SupplierPart.price_ils > 0)
            .group_by(SupplierPart.part_id)
            .subquery()
        )
        stmt = (
            select(SupplierPart, avg_sq.c.avg_price)
            .join(avg_sq, SupplierPart.part_id == avg_sq.c.part_id)
            .where(
                SupplierPart.stock_quantity > 50,
                SupplierPart.price_ils > 0,
                SupplierPart.price_ils < avg_sq.c.avg_price * 0.85,
            )
            .limit(200)
        )
        rows = (await db.execute(stmt)).all()
        created = 0
        for sp, avg_price in rows:
            discount_pct = round(
                (float(avg_price) - float(sp.price_ils)) / float(avg_price) * 100, 2
            )
            db.add(ApprovalQueue(
                entity_type="bulk_deal",
                entity_id=sp.id,
                action="approve_bulk_deal",
                payload={
                    "supplier_part_id": str(sp.id),
                    "supplier_id": str(sp.supplier_id),
                    "part_id": str(sp.part_id),
                    "price_ils": float(sp.price_ils),
                    "avg_market_price_ils": round(float(avg_price), 2),
                    "discount_pct": discount_pct,
                    "stock_quantity": sp.stock_quantity,
                },
            ))
            created += 1
        if created:
            await db.commit()
        return created

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        return "סוכן זה הוא לשימוש פנימי בלבד. כדי לקבל עזרה עם הזמנה או חלקים, פנה לצוות השירות."


# ==============================================================================
# 9. SOCIAL MEDIA MANAGER AGENT
# ==============================================================================

class SocialMediaManagerAgent(BaseAgent):
    name = "social_media_manager_agent"
    model = PREMIUM_MODEL      # premium: creative content generation
    temperature = 0.9
    agent_name = "Noa"          # נועה — social media strategist
    system_prompt = """You are Noa, the Social Media Manager for Auto Spare, an Israeli auto parts platform.

Platforms: Facebook, Instagram, TikTok, Twitter/X, LinkedIn, Telegram.

Content split (weekly):
- 40% educational (tips, guides, how-to)
- 30% commercial (promotions, products)
- 20% engagement (polls, questions)
- 10% UGC (customer photos)

Posting schedule:
- Facebook: 13:00, 19:00
- Instagram: 11:00, 18:00
- TikTok: 08:00, 12:00, 20:00
- LinkedIn: weekdays only

When asked to create a post or content, ALWAYS generate the full post text directly including hashtags.
Generated posts are saved to the social_posts table for scheduling — always provide the full text.
Paid ads and new campaigns require manager approval via the ApprovalQueue before publishing.

CONTENT IDEATION — SEARCH MISS SIGNALS:
- When planning content, check the search_misses table for parts customers searched but couldn't find.
- Turning a search miss into a "coming soon" post builds anticipation and captures demand early.
- Example: "🔜 בקרוב: {part_name} ל-{brand} — הירשמו לקבל התראה!"

LANGUAGE: ALWAYS respond in Hebrew (עברית). Write all posts in Hebrew with relevant Hebrew hashtags.
If the customer writes in Arabic, respond in Arabic. Never respond in any other language.
"""

    async def generate_post(self, topic: str, platform: str, tone: str = "professional") -> str:
        prompt = f"Create a {platform} post about: {topic}. Tone: {tone}. Language: Hebrew. Include relevant hashtags."
        return await self.think([{"role": "user", "content": prompt}])

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        return await self.think(conversation_history + [{"role": "user", "content": message}])


# ==============================================================================
# AGENT REGISTRY
# ==============================================================================

AGENT_MAP = {
    "router_agent": RouterAgent,
    "parts_finder_agent": PartsFinderAgent,
    "sales_agent": SalesAgent,
    "orders_agent": OrdersAgent,
    "finance_agent": FinanceAgent,
    "service_agent": ServiceAgent,
    "security_agent": SecurityAgent,
    "marketing_agent": MarketingAgent,
    "supplier_manager_agent": SupplierManagerAgent,
    "social_media_manager_agent": SocialMediaManagerAgent,
    "tech_agent": TechAgent,
}

# Singleton instances
_agents: Dict[str, BaseAgent] = {}


def get_agent(name: str) -> BaseAgent:
    if name not in _agents:
        agent_class = AGENT_MAP.get(name, ServiceAgent)
        _agents[name] = agent_class()
    return _agents[name]


# ==============================================================================
# MAIN MESSAGE PROCESSOR
# ==============================================================================

async def process_agent_response_for_message(
    user_id: str,
    message: str,
    conversation_id: str,
    db: AsyncSession,
) -> None:
    """
    Background-safe: load conversation, route to agent, call LLM, save assistant message.
    Called via asyncio.create_task with its own DB session.
    """
    # Load conversation
    conv_res = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conversation = conv_res.scalar_one_or_none()
    if not conversation:
        print(f"[BG AGENT] conversation {conversation_id} not found")
        return

    # Load history (last 20 messages)
    hist_res = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc())
        .limit(20)
    )
    history = [{"role": m.role, "content": m.content} for m in hist_res.scalars().all()]

    # Route to correct agent
    router = get_agent("router_agent")
    route_result = await router.route(message, {"history_length": len(history)})
    agent_name = route_result.get("agent", "service_agent")

    conversation.current_agent = agent_name
    conversation.last_message_at = datetime.utcnow()

    # Call agent LLM
    agent = get_agent(agent_name)
    start_time = datetime.utcnow()
    try:
        response_text = await agent.process(message, history, db, user_id=user_id)
    except Exception as e:
        print(f"[BG AGENT ERROR] {agent_name}: {e}")
        response_text = "מצטער, נתקלתי בבעיה. אנא נסה שוב בעוד רגע."
        agent_name = "service_agent"

    exec_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

    # Save assistant message
    assistant_msg = Message(
        conversation_id=conversation.id,
        role="assistant",
        agent_name=agent_name,
        content=response_text,
        content_type="text",
        model_used=getattr(agent, "model", None),
    )
    db.add(assistant_msg)
    await db.flush()

    # Save action log
    db.add(AgentAction(
        message_id=assistant_msg.id,
        agent_name=agent_name,
        action_type="respond",
        action_data={"route_result": route_result},
        success=True,
        execution_time_ms=exec_ms,
    ))
    await db.commit()
    print(f"[BG AGENT] conv={conversation_id} agent={agent_name} {exec_ms}ms")


async def process_user_message(
    user_id: str,
    message: str,
    conversation_id: Optional[str],
    db: AsyncSession,
) -> Dict[str, Any]:
    """
    Main entry point: routes message, calls agent, saves to DB, returns response.
    """
    # ── 1. Get or create conversation ──────────────────────────────────────────
    if conversation_id:
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
    else:
        conversation = None

    if not conversation:
        conversation = Conversation(
            user_id=user_id,
            title=message[:60] + ("..." if len(message) > 60 else ""),
            is_active=True,
            started_at=datetime.utcnow(),
            last_message_at=datetime.utcnow(),
        )
        db.add(conversation)
        await db.flush()

    # ── 2. Load conversation history ───────────────────────────────────────────
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc())
        .limit(20)  # last 20 messages for context
    )
    history_rows = result.scalars().all()
    history = [
        {"role": msg.role, "content": msg.content}
        for msg in history_rows
    ]

    # ── 3. Save user message ───────────────────────────────────────────────────
    user_msg = Message(
        conversation_id=conversation.id,
        role="user",
        content=message,
        content_type="text",
    )
    db.add(user_msg)
    await db.flush()

    # ── 4. Route to correct agent ──────────────────────────────────────────────
    router = get_agent("router_agent")
    route_result = await router.route(message, {"history_length": len(history)})
    agent_name = route_result.get("agent", "service_agent")

    # Update conversation's current agent
    conversation.current_agent = agent_name
    conversation.last_message_at = datetime.utcnow()

    # ── 5. Process with selected agent ────────────────────────────────────────
    agent = get_agent(agent_name)
    start_time = datetime.utcnow()

    try:
        response_text = await agent.process(message, history, db, user_id=str(user_id))
    except Exception as e:
        print(f"[ERROR] Agent {agent_name} failed: {e}")
        response_text = "מצטער, נתקלתי בבעיה. אנא נסה שוב בעוד רגע."
        agent_name = "service_agent"

    exec_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

    # ── 6. Save assistant message ─────────────────────────────────────────────
    assistant_msg = Message(
        conversation_id=conversation.id,
        role="assistant",
        agent_name=agent_name,
        content=response_text,
        content_type="text",
        model_used=agent.model,
    )
    db.add(assistant_msg)
    await db.flush()  # ensure assistant_msg.id is set before AgentAction

    # ── 7. Save agent action log ──────────────────────────────────────────────
    action = AgentAction(
        message_id=assistant_msg.id,
        agent_name=agent_name,
        action_type="respond",
        action_data={"route_result": route_result},
        success=True,
        execution_time_ms=exec_ms,
    )
    db.add(action)

    await db.commit()
    await db.refresh(assistant_msg)

    return {
        "conversation_id": str(conversation.id),
        "message_id": str(assistant_msg.id),
        "agent": agent_name,
        "response": response_text,
        "created_at": assistant_msg.created_at.isoformat(),
        "routing": route_result,
    }
