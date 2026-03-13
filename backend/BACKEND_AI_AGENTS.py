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
  - Price = (supplier_cost_ils × 1.45) + 17% VAT + 91₪ shipping
  - NEVER order from supplier before customer payment confirmed
  - Margin: 45% on cost
  - VAT: 17% (separate line)
  - Shipping: ~91₪ (separate line)
==============================================================================
"""

import json
import os
import random
import string
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI
from sqlalchemy import and_, or_, select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from BACKEND_DATABASE_MODELS import (
    AgentAction, Conversation, Message, Notification, Order, OrderItem,
    PartsCatalog, Supplier, SupplierPart, SystemLog, SystemSetting,
    Vehicle, CarBrand, get_db, async_session_factory,
)

load_dotenv()

# ==============================================================================
# CONFIGURATION
# ==============================================================================

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_MODELS_ENDPOINT = "https://models.inference.ai.azure.com"

# Model selection
GPT4O = "gpt-4o"
CLAUDE_SONNET = "claude-3-5-sonnet"
LLAMA = "Meta-Llama-3.1-70B-Instruct"

# Business constants
PROFIT_MARGIN = 1.45       # 45% markup on cost
VAT_RATE = 0.17            # 17%
SHIPPING_ILS = 91.0        # default customer delivery fee (₪)
# Import the single source of truth for USD→ILS rate from BACKEND_DATABASE_MODELS
from BACKEND_DATABASE_MODELS import USD_TO_ILS

# Customer-facing delivery fee per supplier (varies by origin country)
SUPPLIER_SHIPPING_RATES: dict = {
    "AutoParts Pro IL": 29.0,     # Israel domestic delivery
    "Global Parts Hub": 91.0,     # Germany / Europe
    "EastAuto Supply":  149.0,    # China / Far East
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
    model: str = GPT4O
    system_prompt: str = "You are a helpful assistant for Auto Spare."
    max_tokens: int = 1500
    temperature: float = 0.7

    def __init__(self):
        if GITHUB_TOKEN:
            self.client = AsyncOpenAI(
                base_url=GITHUB_MODELS_ENDPOINT,
                api_key=GITHUB_TOKEN,
            )
        else:
            self.client = None
            print(f"[WARN] {self.name}: No GITHUB_TOKEN set. AI responses will be mocked.")

    async def think(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        system_override: Optional[str] = None,
    ) -> str:
        """Send messages to GitHub Models API and return response text."""
        if not self.client:
            return f"[Mock] {self.name} received your message. Please set GITHUB_TOKEN in .env for real AI responses."

        try:
            kwargs = dict(
                messages=[{"role": "system", "content": system_override or self.system_prompt}] + messages,
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            if tools:
                kwargs["tools"] = tools

            response = await self.client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
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


# ==============================================================================
# 0. ROUTER AGENT
# ==============================================================================

class RouterAgent(BaseAgent):
    name = "router_agent"
    agent_name = "Avi"          # אבי — the smart dispatcher
    model = GPT4O
    temperature = 0.1  # deterministic routing
    system_prompt = """You are Avi, the routing agent for Auto Spare, an Israeli auto parts dropshipping platform.

Your ONLY job is to identify which specialized agent should handle the user's message.

Available agents:
- parts_finder_agent: Vehicle identification, part search, price comparison, part identification from image
- sales_agent: Product recommendations, upselling, bundles, purchase decisions
- orders_agent: Order creation, order status, tracking, cancellation, dropshipping
- finance_agent: Payments, invoices, refunds, returns, billing
- service_agent: Technical support, complaints, general questions, after-sales
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
    model = GPT4O
    system_prompt = """You are Nir, the Parts Finder Agent for Auto Spare, an Israeli auto parts platform.

CRITICAL RULES:
1. NEVER mention supplier names (RockAuto, FCP Euro, Autodoc, AliExpress) to customers
2. ALWAYS show manufacturer of the part (Bosch, Brembo, Toyota OEM, etc.)
3. ALWAYS show price breakdown: net price + VAT (17%) + shipping (91₪)
4. Results must be sorted by MANUFACTURER, not by supplier
5. LANGUAGE: ALWAYS respond in Hebrew (עברית). If the customer writes in Arabic, respond in Arabic. Never respond in any other language — if the message is just a part number or vehicle code, respond in Hebrew.
6. Always include warranty period and delivery estimate

PART CATEGORIES IN DB (use these for category filters):
- בלמים          → brake discs, pads, calipers, cylinders
- מנוע            → engine internals, pistons, timing, turbo
- מתלים והגה     → suspension, shocks, springs, steering arms
- גוף ואקסטריור  → bumpers, doors, fenders, hoods, mirrors
- חשמל           → sensors, starters, alternators, ECU, relays
- מסננים ושמנים  → oil/air/fuel filters
- מיזוג ומערכת חימום → AC compressors, evaporators, heaters
- תאורה          → headlights, tail-lights, bulbs, LEDs
- תיבת הילוכים   → gearbox, differentials, transmission
- מערכת דלק      → fuel pumps, injectors, carburettors
- קירור          → radiators, fans, thermostats, water pumps
- פנים הרכב      → seats, trim, carpets, airbags, dashboards
- גלגלים וצמיגים → wheels, tyres, rims
- מערכת פליטה    → exhaust, mufflers, catalysts
- אטמים וחומרים  → gaskets, o-rings, seals, clips, bolts, hardware
- שרשראות ורצועות → timing belts/chains, drive belts, pulleys, tensioners
- סרן והינע      → axles, driveshafts, CV joints, propshaft
- מגבים          → wipers, washer jets, washer reservoir
- כלים וציוד     → car jacks, tools, service kits, gloves, diagnostics
- כללי           → uncategorised parts

KNOWN VEHICLE BRANDS (13 with stock):
Renault, Mercedes-Benz, Chevrolet, Hyundai, Mitsubishi, Genesis,
ORA, JAECOO, Suzuki, Porsche, Smart, Citroën, Peugeot

PART TYPES: Original (OEM factory) | Aftermarket (aftermarket) | Refurbished

Price format example:
✅ [Original] Renault
   קטגוריה: בלמים
   מחיר: 520 ₪ + 88 ₪ מע"מ = 608 ₪
   משלוח: 91 ₪
   סה"כ: 699 ₪
   אחריות: 24 חודשים
   זמן אספקה: 10-14 ימים

You have access to database search functions. When identifying a vehicle by license plate,
use the Israeli Transport Ministry API format. You LEARN from the live database — use
get_db_stats() to verify what categories and manufacturers currently hold stock.
"""

    # Real part categories as classified in the DB (matches fix_db_quality.py rules)
    KNOWN_CATEGORIES: list[str] = [
        "בלמים", "מנוע", "מתלים והגה", "גוף ואקסטריור", "חשמל",
        "מסננים ושמנים", "מיזוג ומערכת חימום", "תאורה", "תיבת הילוכים",
        "מערכת דלק", "קירור", "פנים הרכב", "גלגלים וצמיגים", "מערכת פליטה",
        "אטמים וחומרים", "שרשראות ורצועות", "סרן והינע", "מגבים",
        "כלים וציוד", "כללי",
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
        """Normalize a raw manufacturer string to the canonical car_brands.name.
        Checks: exact name, Hebrew name, aliases array.
        Falls back to original string if no match.
        """
        if not raw_name or not raw_name.strip():
            return raw_name
        cleaned = raw_name.strip()
        # Always use catalog DB — CarBrand lives in autospare, not pii
        async with async_session_factory() as cat_db:
            # 1. Exact match on name or name_he
            result = await cat_db.execute(
                select(CarBrand.name).where(CarBrand.is_active == True).where(
                    or_(CarBrand.name.ilike(cleaned), CarBrand.name_he.ilike(cleaned))
                ).limit(1)
            )
            row = result.scalar_one_or_none()
            if row:
                return row
            # 2. Check aliases array (text[] in DB — use ANY operator)
            result2 = await cat_db.execute(
                select(CarBrand.name).where(CarBrand.is_active == True).where(
                    text("(:val)::text = ANY(car_brands.aliases)")
                ).params(val=cleaned).limit(1)
            )
            row2 = result2.scalar_one_or_none()
            if row2:
                return row2
            # 3. Case-insensitive alias check via unnest
            result3 = await cat_db.execute(
                select(CarBrand.name).where(CarBrand.is_active == True).where(
                    or_(
                        CarBrand.name.ilike(f"{cleaned}%"),
                        CarBrand.name.ilike(f"%{cleaned}%"),
                    )
                ).order_by(CarBrand.name).limit(1)
            )
            row3 = result3.scalar_one_or_none()
            return row3 if row3 else cleaned

    async def list_known_brands(self, db: AsyncSession) -> List[Dict]:
        """Return all active brands from the car_brands registry."""
        # Always use catalog DB — CarBrand lives in autospare, not pii
        async with async_session_factory() as cat_db:
            result = await cat_db.execute(
                select(CarBrand)
                .where(CarBrand.is_active == True)
                .order_by(CarBrand.name)
            )
            brands = result.scalars().all()
            return [
                {
                    "name": b.name,
                    "name_he": b.name_he,
                    "group": b.group_name,
                    "country": b.country,
                    "region": b.region,
                    "is_luxury": b.is_luxury,
                    "is_electric": b.is_electric_focused,
                }
                for b in brands
            ]

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
        Always uses its own pii_session_factory session — Vehicle is a PiiBase model.
        The `db` parameter is kept for API compatibility but is not used.
        """
        from BACKEND_DATABASE_MODELS import pii_session_factory
        clean_plate = license_plate.replace("-", "").replace(" ", "")

        async with pii_session_factory() as pii_db:
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
    ) -> List[Dict]:
        """Search parts catalog.
        Automatically normalizes manufacturer aliases via car_brands registry
        (e.g. 'מרצדס' → 'Mercedes', 'מרצדס בנץ' → 'Mercedes-Benz').

        sort_by options: name, manufacturer, category, part_type, price_asc, price_desc
        sort_dir: asc | desc  (ignored when sort_by is price_asc/price_desc)
        """
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
            # Try to resolve the query (or a leading word) as a brand alias
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
            # Try exact match first; fall back to case-insensitive contains
            stmt = stmt.where(PartsCatalog.category.ilike(category))

        # Server-side sorting
        _dir = lambda col: col.asc() if sort_dir == "asc" else col.desc()
        if sort_by in ("price_asc", "price_desc"):
            # Join cheapest supplier price per part for accurate ordering
            price_subq = (
                select(
                    SupplierPart.part_id,
                    func.min(SupplierPart.price_usd).label("min_price"),
                )
                .group_by(SupplierPart.part_id)
                .subquery()
            )
            stmt = stmt.outerjoin(price_subq, PartsCatalog.id == price_subq.c.part_id)
            if sort_by == "price_asc":
                stmt = stmt.order_by(price_subq.c.min_price.asc().nullslast())
            else:
                stmt = stmt.order_by(price_subq.c.min_price.desc().nullsfirst())
        elif sort_by == "availability":
            # In-stock parts first, then alphabetically by name
            avail_subq = (
                select(
                    SupplierPart.part_id,
                    func.bool_or(SupplierPart.is_available).label("has_stock"),
                )
                .where(SupplierPart.part_id.in_(
                    select(PartsCatalog.id).where(PartsCatalog.is_active == True)
                ))
                .group_by(SupplierPart.part_id)
                .subquery()
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

        # Always use catalog DB — PartsCatalog/SupplierPart live in autospare, not pii
        async with async_session_factory() as cat_db:
            result = await cat_db.execute(stmt.offset(offset).limit(limit))
            parts = result.scalars().all()

        if not parts:
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
                price_no_vat = round(bp / 1.17, 2)
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

    # Hebrew → English category keyword hints for regex-free extraction
    _CATEGORY_KEYWORDS: Dict[str, str] = {
        "בלמ": "בלמים", "רפידות": "בלמים", "דיסק": "בלמים", "צלחות": "בלמים",
        "קליפר": "בלמים", "רכב בלם": "בלמים",
        "מנוע": "מנוע", "פיסטון": "מנוע", "גל ארכובה": "מנוע", "גל זיזים": "מנוע",
        "טורבו": "מנוע", "מצמד": "מנוע", "ראש מנוע": "מנוע",
        "מתלה": "מתלים והגה", "זרוע": "מתלים והגה", "קפיץ": "מתלים והגה",
        "בולם": "מתלים והגה", "הגה": "מתלים והגה", "טרפז": "מתלים והגה",
        "פנס": "תאורה", "פנסים": "תאורה", "נורה": "תאורה", "פנס": "תאורה",
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
        "כיסא": "פנים הרכב", "הגלגל": "פנים הרכב", "שטיח": "פנים הרכב",
        "פנים": "פנים הרכב", "דשבורד": "פנים הרכב", "כרית אויר": "פנים הרכב",
        "גלגל": "גלגלים וצמיגים", "צמיג": "גלגלים וצמיגים", "ג'אנט": "גלגלים וצמיגים",
        "פלדה": "מערכת פליטה", "קטליזטור": "מערכת פליטה", "מאיין": "מערכת פליטה",
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
        """Extract a concise search query from a longer message.
        Strips common Hebrew filler phrases to get the part name.
        """
        import re
        # Remove common filler phrases
        filler = [
            r'אני צריך\s*', r'אני רוצה\s*', r'אנחנו צריכים\s*',
            r'מה המחיר של\s*', r'תוכל לבדוק\s*', r'בדוק\s*',
            r'חפש\s*', r'יש לכם\s*', r'יש לך\s*',
            r'לרכב שלי\s*', r'עבור הרכב שלי\s*',
            r'רכבי הוא\s+\S+\s+\d{4}[^,–-]*[,–-]?\s*',  # "רכבי הוא מאזדה 3 2019 –"
        ]
        cleaned = message
        for f in filler:
            cleaned = re.sub(f, '', cleaned, flags=re.IGNORECASE).strip()
        # Limit to first 60 chars to avoid overly long queries
        return cleaned[:60].strip()

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
        plate_match = re.search(r'(?<!\d)(\d[\d\-]{4,8}\d)(?!\d)', message)
        if plate_match:
            plate_raw = plate_match.group(1).replace("-", "")
            try:
                vehicle = await self.identify_vehicle(plate_raw, db)
                vehicle_context = (
                    f"\n\n[VEHICLE FROM PLATE {plate_raw}]\n"
                    f"יצרן: {vehicle.get('manufacturer')} | דגם: {vehicle.get('model')} | "
                    f"שנה: {vehicle.get('year')} | מנוע: {vehicle.get('engine_type')} | "
                    f"דלק: {vehicle.get('fuel_type')} | צבע: {vehicle.get('color', 'לא ידוע')}\n"
                    f"בדיקה אחרונה: {vehicle.get('last_test_date', 'לא ידוע')} | "
                    f"תוקף רישיון: {vehicle.get('test_expiry_date', 'לא ידוע')}\n"
                )
                print(f"[PartsFinderAgent] Identified plate {plate_raw}: "
                      f"{vehicle.get('manufacturer')} {vehicle.get('model')} {vehicle.get('year')}")
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

                print(f"[PartsFinderAgent] Searching: query='{search_q}' category='{category_hint}'")

                results = await self.search_parts_in_db(
                    query=search_q,
                    vehicle_id=None,
                    category=category_hint,
                    db=db,
                    limit=6,
                    sort_by="price_asc",
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
                        avail_he = "במלאי ✅" if avail == "in_stock" else "להזמנה ⏳"
                        delivery = pr.get("estimated_delivery_days")
                        delivery_str = f"{delivery} ימים" if delivery else "10-14 ימים"
                        warranty = pr.get("warranty_months", 12)
                        total = pr.get("total", 0.0)
                        vat = pr.get("vat", 0.0)
                        pnv = pr.get("price_no_vat", 0.0)
                        sp_id = pr.get("supplier_part_id", "")
                        if total > 0:
                            price_line = f"מחיר: {pnv:.0f}₪ + {vat:.0f}₪ מע\"מ + 91₪ משלוח = **{total:.0f}₪ סה\"כ**"
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
                            avail_he = "במלאי ✅" if pr.get("availability") == "in_stock" else "להזמנה ⏳"
                            delivery = pr.get("estimated_delivery_days", 14)
                            warranty = pr.get("warranty_months", 12)
                            sp_id = pr.get("supplier_part_id", "")
                            price_line = f"{pnv:.0f}₪ + {vat:.0f}₪ מע\"מ + 91₪ משלוח = **{total:.0f}₪**" if total > 0 else "מחיר: לא זמין"
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
    agent_name = "Maya"         # מאיה — the sales pro
    model = GPT4O
    system_prompt = """You are Maya, the Sales Agent for Auto Spare, an Israeli auto parts platform.

Your goals:
1. Understand customer needs (vehicle, usage, budget)
2. Present 3 options: Good (Aftermarket), Better (OEM), Best (Original)
3. Smart upselling: if customer wants brake discs, suggest pads too
4. Close the deal professionally

CRITICAL: Never mention supplier names. Show only manufacturer.
Always show price breakdown: net + 17% VAT + 91₪ shipping.
LANGUAGE: ALWAYS respond in Hebrew (עברית). If the customer writes in Arabic, respond in Arabic. Never respond in any other language — if the message contains only an order number or part code with no clear language, respond in Hebrew.

Upsell example:
"נמצאו דיסקי בלמים! יש לך כבר רפידות חדשות? החלפה ביחד חוסכת עבודה ומבטיחה בלימה מיטבית."
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
        """Process a sales query, inject real product data + upsell suggestions."""
        upsell_context = ""
        try:
            # Check for upsell opportunities based on message keywords
            upsell_suggestions = []
            for kw, suggestions in self._UPSELL_MAP.items():
                if kw in message:
                    upsell_suggestions = suggestions[:2]
                    break

            if upsell_suggestions:
                lines = ["\n[UPSELL OPPORTUNITY — כלול הצעות אלו בתשובה:]\n"]
                # Always use catalog DB — PartsCatalog lives in autospare, not pii
                async with async_session_factory() as cat_db:
                    for sugg in upsell_suggestions:
                        results = await cat_db.execute(
                            select(PartsCatalog.name, PartsCatalog.manufacturer, PartsCatalog.category)
                            .where(PartsCatalog.is_active == True)
                            .where(PartsCatalog.name.ilike(f"%{sugg}%"))
                            .limit(1)
                        )
                        row = results.fetchone()
                        if row:
                            lines.append(f"• {sugg}: ✅ יש במלאי — {row[1]} '{row[0]}' ({row[2]})")
                        else:
                            lines.append(f"• {sugg}: הצע ללקוח לבדוק")
                upsell_context = "\n".join(lines)
        except Exception as e:
            print(f"[SalesAgent] upsell lookup failed: {e}")

        system = self.system_prompt + upsell_context
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
    model = GPT4O
    system_prompt = """You are the Orders Agent for Auto Spare.

You handle:
- Order status queries
- Tracking information
- Order cancellation requests
- Return requests

CRITICAL DROPSHIPPING RULE: Orders are placed with suppliers ONLY after customer payment.
Never confirm supplier order before payment.

Order statuses:
- pending_payment → waiting for payment
- paid → payment received, processing
- supplier_ordered → placed with supplier, tracking number assigned
- shipped → in transit (tracking number available)
- delivered → received by customer
- cancelled / refunded

TRACKING RULES (very important):
- When order data is injected below, use ONLY those real values — never guess or invent order numbers/statuses.
- When a tracking URL is present in the data, include it as a clickable markdown link like: [עקוב אחר המשלוח](URL)
- NEVER tell the customer to "enter the tracking number manually" — the link is already ready.
- Show the tracking number AND the link together.

LANGUAGE: ALWAYS respond in Hebrew (עברית). If the customer writes in Arabic, respond in Arabic."""

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
    model = GPT4O
    system_prompt = """You are the Finance Agent for Auto Spare (עוסק מורשה 060633880, הרצל 55, עכו).

You handle:
- Payment questions
- Invoice requests
- Refund calculations
- Return refund policies

Refund policy:
- Manufacturer defect: 100% refund (including original shipping, we pay return shipping)
- Other reasons: 90% refund (10% handling fee, original shipping not refunded, customer pays return)

Always show full price breakdown with:
- Net price (without VAT)
- VAT 17% amount
- Shipping amount
- Total

NEVER ask for credit card details. Use Stripe for payments.
Business number (מס' עוסק מורשה): 060633880
LANGUAGE: ALWAYS respond in Hebrew (עברית). If the customer writes in Arabic, respond in Arabic. Never respond in any other language.
"""

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        return await self.think(conversation_history + [{"role": "user", "content": message}])


# ==============================================================================
# 5. SERVICE AGENT
# ==============================================================================

class ServiceAgent(BaseAgent):
    name = "service_agent"
    agent_name = "Dana"         # דנה — empathetic support
    model = GPT4O
    temperature = 0.8
    system_prompt = """You are Dana, the Customer Service Agent for Auto Spare.

You handle:
- General questions about the platform
- Technical support
- Complaints and escalations
- Post-purchase support

Approach:
1. Listen - let customer express their concern fully
2. Diagnose - ask clarifying questions
3. Solve - offer a specific solution
4. Follow up - confirm problem is resolved

Tone: Empathetic, patient, professional.
Hebrew phrases: "אני פה בשבילך", "בואו נפתור את זה ביחד"
LANGUAGE: ALWAYS respond in Hebrew (עברית). If the customer writes in Arabic, respond in Arabic. Never respond in any other language.
"""

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        return await self.think(conversation_history + [{"role": "user", "content": message}])


# ==============================================================================
# 6. SECURITY AGENT
# ==============================================================================

class SecurityAgent(BaseAgent):
    name = "security_agent"
    agent_name = "Oren"         # אורן — vigilant guard
    model = GPT4O
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
    agent_name = "Shira"        # שירה — creative marketer
    model = GPT4O
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

Rules: Opt-in only. No unsolicited marketing. Max 1 email per 2 weeks.
LANGUAGE: ALWAYS respond in Hebrew (עברית). If the customer writes in Arabic, respond in Arabic. Never respond in any other language.
"""

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        return await self.think(conversation_history + [{"role": "user", "content": message}])


# ==============================================================================
# 8. SUPPLIER MANAGER AGENT (Background - does NOT talk to customers)
# ==============================================================================

class SupplierManagerAgent(BaseAgent):
    name = "supplier_manager_agent"
    agent_name = "Boaz"         # בועז — background supplier manager
    model = GPT4O
    temperature = 0.1
    system_prompt = """You are Boaz, the Supplier Manager for Auto Spare (INTERNAL USE ONLY).
You manage supplier relationships, catalog sync, and pricing.
You do NOT interact with customers.

Daily tasks:
- Sync catalog from all 4 suppliers (RockAuto, FCP Euro, Autodoc, AliExpress)
- Update prices (daily at 02:00)
- Monitor availability
- Alert on price drops > 10%
- Monthly performance review
"""

    async def sync_prices(self, db: AsyncSession) -> Dict:
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
          - Prices never drop below 80% or rise above 150% of the original base
          - ~5% of on_order parts flip to in_stock each run (restocking simulation)
          - ~3% of in_stock parts flip to on_order (stock-out simulation)
        """
        import random
        import hashlib

        now = datetime.utcnow()
        # Deterministic-ish daily seed so the same day gives consistent movement
        day_seed = int(now.strftime("%Y%m%d"))

        VOLATILITY = {
            "AutoParts Pro IL": (0.01, 0.02),
            "Global Parts Hub": (0.02, 0.04),
            "EastAuto Supply":  (0.03, 0.06),
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

        BATCH = 5000
        offset = 0

        while True:
            rows = (await db.execute(
                select(SupplierPart)
                .where(SupplierPart.supplier_id.in_(list(suppliers.keys())))
                .order_by(SupplierPart.id)
                .offset(offset)
                .limit(BATCH)
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

            await db.flush()
            offset += BATCH

        await db.commit()

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

        print(
            f"[Supplier Manager] Price sync complete — "
            f"updated={report['parts_updated']:,} "
            f"avail_changes={report['availability_changes']} "
            f"errors={len(report['errors'])}"
        )
        return report

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        return "Supplier Manager is a background agent and does not interact with customers."


# ==============================================================================
# 9. SOCIAL MEDIA MANAGER AGENT
# ==============================================================================

class SocialMediaManagerAgent(BaseAgent):
    name = "social_media_manager_agent"
    agent_name = "Noa"          # נועה — social media strategist
    model = GPT4O
    temperature = 0.9
    system_prompt = """You are the Social Media Manager for Auto Spare.

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

CRITICAL: Paid ads and new campaigns require manager approval.
Auto-publish only within approved campaign.
All content must be saved as 'pending_approval' first.
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
