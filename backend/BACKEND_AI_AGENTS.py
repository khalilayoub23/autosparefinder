"""
==============================================================================
AUTO SPARE - AI AGENTS (GitHub Models API)
==============================================================================
10 Specialized Agents + Router Agent:
  0. RouterAgent        - Route messages to the right agent
  1. PartsFinderAgent   - Search parts, identify vehicles, compare prices
  2. SalesAgent         - Recommendations, upselling, closing deals
  3. OrdersAgent        - Order management, tracking, dropshipping
  4. FinanceAgent       - Payments, invoices, refunds
  5. ServiceAgent       - Support, complaints, knowledge base
  6. SecurityAgent      - 2FA, account security, suspicious activity
  7. MarketingAgent     - Promotions, coupons, newsletter, loyalty
  8. SupplierManagerAgent - Background catalog sync, price updates
  9. SocialMediaManagerAgent - Social content, scheduling, engagement

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
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from BACKEND_DATABASE_MODELS import (
    AgentAction, Conversation, Message, PartsCatalog, Supplier,
    SupplierPart, SystemSetting, Vehicle, get_db,
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
PROFIT_MARGIN = 1.45       # 45% margin
VAT_RATE = 0.17            # 17%
SHIPPING_ILS = 91.0        # default shipping
USD_TO_ILS = 3.65          # exchange rate


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
    ) -> str:
        """Send messages to GitHub Models API and return response text."""
        if not self.client:
            return f"[Mock] {self.name} received your message. Please set GITHUB_TOKEN in .env for real AI responses."

        try:
            kwargs = dict(
                messages=[{"role": "system", "content": self.system_prompt}] + messages,
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
    ) -> Dict[str, float]:
        """Calculate final customer price from supplier cost."""
        cost_ils = (supplier_price_usd + shipping_cost_usd) * USD_TO_ILS
        price_no_vat = round(cost_ils * PROFIT_MARGIN, 2)
        vat = round(price_no_vat * VAT_RATE, 2)
        total = round(price_no_vat + vat + SHIPPING_ILS, 2)
        profit = round(price_no_vat - cost_ils, 2)
        return {
            "cost_ils": round(cost_ils, 2),
            "price_no_vat": price_no_vat,
            "vat": vat,
            "shipping": SHIPPING_ILS,
            "total": total,
            "profit": profit,
        }


# ==============================================================================
# 0. ROUTER AGENT
# ==============================================================================

class RouterAgent(BaseAgent):
    name = "router_agent"
    model = GPT4O
    temperature = 0.1  # deterministic routing
    system_prompt = """You are a routing agent for Auto Spare, an Israeli auto parts dropshipping platform.

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
    model = GPT4O
    system_prompt = """You are the Parts Finder Agent for Auto Spare, an Israeli auto parts platform.

CRITICAL RULES:
1. NEVER mention supplier names (RockAuto, FCP Euro, Autodoc, AliExpress) to customers
2. ALWAYS show manufacturer of the part (Bosch, Brembo, Toyota OEM, etc.)
3. ALWAYS show price breakdown: net price + VAT (17%) + shipping (91₪)
4. Results must be sorted by MANUFACTURER, not by supplier
5. Respond in the SAME LANGUAGE the customer uses
6. Always include warranty period and delivery estimate

Price format example:
✅ [OEM] Toyota
   מחיר: 520 ₪ + 88 ₪ מע"מ = 608 ₪
   משלוח: 91 ₪
   סה"כ: 699 ₪
   אחריות: 24 חודשים
   זמן אספקה: 10-14 ימים

You have access to database search functions. When identifying a vehicle by license plate,
use the Israeli Transport Ministry API format.
"""

    async def identify_vehicle(self, license_plate: str, db: AsyncSession) -> Dict:
        """Identify vehicle from license plate (Israeli transport ministry API)."""
        # Check cache in DB
        result = await db.execute(
            select(Vehicle).where(Vehicle.license_plate == license_plate)
        )
        vehicle = result.scalar_one_or_none()

        if vehicle and vehicle.cached_at:
            cache_age = (datetime.utcnow() - vehicle.cached_at).days
            if cache_age < 90:
                return {
                    "id": str(vehicle.id),
                    "manufacturer": vehicle.manufacturer,
                    "model": vehicle.model,
                    "year": vehicle.year,
                    "engine_type": vehicle.engine_type,
                }

        # Call Israeli Transport Ministry API
        vehicle_data = await self._call_gov_api(license_plate)
        if not vehicle_data:
            raise Exception(f"Vehicle with plate {license_plate} not found")

        # Save/update in DB
        if vehicle:
            vehicle.manufacturer = vehicle_data.get("manufacturer", "")
            vehicle.model = vehicle_data.get("model", "")
            vehicle.year = vehicle_data.get("year", 0)
            vehicle.engine_type = vehicle_data.get("engine_type")
            vehicle.gov_api_data = vehicle_data
            vehicle.cached_at = datetime.utcnow()
        else:
            vehicle = Vehicle(
                license_plate=license_plate,
                manufacturer=vehicle_data.get("manufacturer", ""),
                model=vehicle_data.get("model", ""),
                year=vehicle_data.get("year", 0),
                engine_type=vehicle_data.get("engine_type"),
                gov_api_data=vehicle_data,
                cached_at=datetime.utcnow(),
            )
            db.add(vehicle)

        await db.commit()
        await db.refresh(vehicle)

        return {
            "id": str(vehicle.id),
            "manufacturer": vehicle.manufacturer,
            "model": vehicle.model,
            "year": vehicle.year,
            "engine_type": vehicle.engine_type,
        }

    async def _call_gov_api(self, license_plate: str) -> Optional[Dict]:
        """Call Israeli Transport Ministry API (data.gov.il)."""
        clean_plate = license_plate.replace("-", "").replace(" ", "")
        url = "https://data.gov.il/api/3/action/datastore_search"
        params = {
            "resource_id": "053cea08-09bc-40ec-8f7a-156f0677aff3",
            "filters": json.dumps({"mispar_rechev": clean_plate}),
            "limit": 1,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params)
                data = resp.json()
                records = data.get("result", {}).get("records", [])
                if records:
                    r = records[0]
                    return {
                        "license_plate": clean_plate,
                        "manufacturer": r.get("tozeret_nm", "Unknown"),
                        "model": r.get("kinuy_mishari", "Unknown"),
                        "year": int(r.get("shnat_yitzur", 0) or 0),
                        "engine_type": r.get("sug_delek_nm"),
                        "color": r.get("tzeva_rechev"),
                    }
        except Exception as e:
            print(f"[ERROR] Gov API failed: {e}")
        return None

    async def search_parts_in_db(
        self,
        query: str,
        vehicle_id: Optional[str],
        category: Optional[str],
        db: AsyncSession,
        limit: int = 20,
    ) -> List[Dict]:
        """Search parts catalog."""
        stmt = select(PartsCatalog).where(PartsCatalog.is_active == True)

        if query:
            stmt = stmt.where(
                or_(
                    PartsCatalog.name.ilike(f"%{query}%"),
                    PartsCatalog.manufacturer.ilike(f"%{query}%"),
                    PartsCatalog.sku.ilike(f"%{query}%"),
                )
            )
        if category:
            stmt = stmt.where(PartsCatalog.category == category)

        result = await db.execute(stmt.limit(limit))
        parts = result.scalars().all()

        output = []
        for part in parts:
            # Get best supplier price
            sp_result = await db.execute(
                select(SupplierPart, Supplier)
                .join(Supplier)
                .where(
                    and_(
                        SupplierPart.part_id == part.id,
                        SupplierPart.is_available == True,
                        Supplier.is_active == True,
                    )
                )
                .order_by(Supplier.priority.asc())
                .limit(1)
            )
            sp_row = sp_result.first()

            pricing = None
            if sp_row:
                sp, supplier = sp_row
                pricing = self.calculate_customer_price(float(sp.price_usd))

            output.append({
                "id": str(part.id),
                "name": part.name,
                "manufacturer": part.manufacturer,
                "category": part.category,
                "part_type": part.part_type,
                "description": part.description,
                "sku": part.sku,
                "pricing": pricing,
                "warranty_months": sp_row[0].warranty_months if sp_row else 12,
            })

        return output

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession) -> str:
        """Process a parts-related message."""
        return await self.think(conversation_history + [{"role": "user", "content": message}])


# ==============================================================================
# 2. SALES AGENT
# ==============================================================================

class SalesAgent(BaseAgent):
    name = "sales_agent"
    model = GPT4O
    system_prompt = """You are the Sales Agent for Auto Spare, an Israeli auto parts platform.

Your goals:
1. Understand customer needs (vehicle, usage, budget)
2. Present 3 options: Good (Aftermarket), Better (OEM), Best (Original)
3. Smart upselling: if customer wants brake discs, suggest pads too
4. Close the deal professionally

CRITICAL: Never mention supplier names. Show only manufacturer.
Always show price breakdown: net + 17% VAT + 91₪ shipping.
Respond in customer's language.

Upsell example:
"נמצאו דיסקי בלמים! יש לך כבר רפידות חדשות? החלפה ביחד חוסכת עבודה ומבטיחה בלימה מיטבית."
"""

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession) -> str:
        return await self.think(conversation_history + [{"role": "user", "content": message}])


# ==============================================================================
# 3. ORDERS AGENT
# ==============================================================================

class OrdersAgent(BaseAgent):
    name = "orders_agent"
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
- supplier_ordered → placed with supplier
- shipped → in transit (tracking number available)
- delivered → received by customer
- cancelled / refunded

Respond in customer's language. Be proactive with updates.
"""

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession) -> str:
        return await self.think(conversation_history + [{"role": "user", "content": message}])


# ==============================================================================
# 4. FINANCE AGENT
# ==============================================================================

class FinanceAgent(BaseAgent):
    name = "finance_agent"
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
"""

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession) -> str:
        return await self.think(conversation_history + [{"role": "user", "content": message}])


# ==============================================================================
# 5. SERVICE AGENT
# ==============================================================================

class ServiceAgent(BaseAgent):
    name = "service_agent"
    model = GPT4O
    temperature = 0.8
    system_prompt = """You are the Customer Service Agent for Auto Spare.

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
Respond in customer's language.
"""

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession) -> str:
        return await self.think(conversation_history + [{"role": "user", "content": message}])


# ==============================================================================
# 6. SECURITY AGENT
# ==============================================================================

class SecurityAgent(BaseAgent):
    name = "security_agent"
    model = GPT4O
    temperature = 0.2
    system_prompt = """You are the Security Agent for Auto Spare.

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
"""

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession) -> str:
        return await self.think(conversation_history + [{"role": "user", "content": message}])


# ==============================================================================
# 7. MARKETING AGENT
# ==============================================================================

class MarketingAgent(BaseAgent):
    name = "marketing_agent"
    model = GPT4O
    system_prompt = """You are the Marketing Agent for Auto Spare.

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
"""

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession) -> str:
        return await self.think(conversation_history + [{"role": "user", "content": message}])


# ==============================================================================
# 8. SUPPLIER MANAGER AGENT (Background - does NOT talk to customers)
# ==============================================================================

class SupplierManagerAgent(BaseAgent):
    name = "supplier_manager_agent"
    model = GPT4O
    temperature = 0.1
    system_prompt = """You are the Supplier Manager for Auto Spare (INTERNAL USE ONLY).
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
        """Run daily price sync (called by scheduled job)."""
        result = await db.execute(
            select(Supplier).where(Supplier.is_active == True)
        )
        suppliers = result.scalars().all()

        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "suppliers_checked": len(suppliers),
            "parts_updated": 0,
            "errors": [],
        }

        # TODO: Call each supplier's API/scraper to update prices
        # This is a background job - implement per supplier

        print(f"[Supplier Manager] Price sync completed: {report}")
        return report

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession) -> str:
        return "Supplier Manager is a background agent and does not interact with customers."


# ==============================================================================
# 9. SOCIAL MEDIA MANAGER AGENT
# ==============================================================================

class SocialMediaManagerAgent(BaseAgent):
    name = "social_media_manager_agent"
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

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession) -> str:
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
        response_text = await agent.process(message, history, db)
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
