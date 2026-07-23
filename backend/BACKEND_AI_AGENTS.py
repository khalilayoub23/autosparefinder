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

All agents run on Cerebras (AGENTS_DEFAULT_MODEL, currently gpt-oss-120b).

CRITICAL BUSINESS RULES (enforced in prompts & code):
  - NEVER expose supplier name to customer - show manufacturer only
  - NEVER expose internal pricing formulas, multipliers, or margin details to customers
  - NEVER order from supplier before customer payment confirmed
  - VAT: 18% for local Israeli suppliers only (separate line when applicable)
  - Shipping: customer-facing fee by supplier/origin
==============================================================================
"""

import json
import os
import re
import random
import string
import asyncio
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, quote
from uuid import UUID as _UUID, uuid4

import logging

import httpx
from dotenv import load_dotenv
from sqlalchemy import and_, or_, select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from hf_client import hf_embed, hf_text, hf_text_fast

from BACKEND_DATABASE_MODELS import (
    AgentAction, AgentSharedMemory, AgentUsageLog, ApprovalQueue, CatalogVersion, Conversation, Message, Notification, Order, OrderItem,
    PartsCatalog, Supplier, SupplierPart, SystemLog, SystemSetting,
    User, Vehicle, CarBrand, TruckBrand, PriceHistory, get_db, async_session_factory,
)
from BACKEND_AUTH_SECURITY import publish_notification
from resilience import retry_with_backoff
from agent_todo_utils import get_active_agent_todos, todo_requests_ranked_first
from manufacturer_normalization import (
    canonicalize_vehicle_model_for_manufacturer,
    normalize_manufacturer_name,
    normalize_vehicle_model_name,
)

load_dotenv()

logger = logging.getLogger(__name__)

# Cap fire-and-forget asyncio.create_task() fan-out (mirrors routes/utils._TASK_SEMAPHORE).
_TASK_SEMAPHORE = asyncio.Semaphore(50)

_SHARED_MEMORY_MAX_ITEMS = 8
_SHARED_MEMORY_MAX_VALUE_LEN = 280


def _safe_uuid(value: Any) -> Optional[_UUID]:
    try:
        return _UUID(str(value))
    except Exception:
        return None


def _truncate_memory_value(value: Any, max_len: int = _SHARED_MEMORY_MAX_VALUE_LEN) -> str:
    text_value = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text_value) <= max_len:
        return text_value
    return text_value[: max_len - 1].rstrip() + "..."


def _compact_preview(value: Any, max_len: int = 240) -> str:
    return _truncate_memory_value(value, max_len=max_len)


def _render_shared_memory_prompt(memory_rows: List[Dict[str, Any]]) -> str:
    if not memory_rows:
        return ""

    lines: List[str] = []
    for row in memory_rows[:_SHARED_MEMORY_MAX_ITEMS]:
        key = str(row.get("memory_key") or "context").replace("_", " ")
        value = _truncate_memory_value(row.get("memory_value"))
        if key and value:
            lines.append(f"- {key}: {value}")

    if not lines:
        return ""

    return "Known customer context from shared memory:\n" + "\n".join(lines)


def _inject_shared_memory_context(history: List[Dict[str, str]], shared_memory_prompt: str) -> List[Dict[str, str]]:
    if not shared_memory_prompt:
        return history

    return [{
        "role": "system",
        "content": (
            "[SHARED MEMORY]\n"
            f"{shared_memory_prompt}\n"
            "Use this context when relevant, but do not mention shared memory explicitly."
        ),
    }] + history[-20:]


def _build_vehicle_memory_summary(vehicle_profile: Dict[str, Any]) -> str:
    return ", ".join(
        part
        for part in [
            str(vehicle_profile.get("manufacturer") or "").strip(),
            str(vehicle_profile.get("model") or "").strip(),
            str(vehicle_profile.get("year") or "").strip(),
            str(vehicle_profile.get("engine_type") or "").strip(),
        ]
        if part
    )


def _extract_shared_memory_updates(
    context_data: Dict[str, Any],
    agent_name: str,
) -> List[Dict[str, Any]]:
    updates: List[Dict[str, Any]] = []

    preferred_lang = str(context_data.get("preferred_lang") or "").strip()
    if preferred_lang:
        updates.append({
            "scope": "user",
            "memory_key": "preferred_language",
            "memory_value": preferred_lang,
            "importance": 3,
            "agent_name": agent_name,
        })

    license_plate = str(context_data.get("license_plate") or "").strip()
    if license_plate:
        updates.append({
            "scope": "conversation",
            "memory_key": "license_plate",
            "memory_value": license_plate,
            "importance": 4,
            "agent_name": agent_name,
        })

    last_part_query = str(context_data.get("last_part_query") or "").strip()
    if last_part_query:
        updates.append({
            "scope": "conversation",
            "memory_key": "last_part_query",
            "memory_value": last_part_query,
            "importance": 2,
            "agent_name": agent_name,
        })

    vehicle_profile = context_data.get("vehicle_profile")
    if isinstance(vehicle_profile, dict):
        summary = _build_vehicle_memory_summary(vehicle_profile)
        if summary:
            updates.append({
                "scope": "conversation",
                "memory_key": "vehicle_profile_summary",
                "memory_value": summary,
                "importance": 4,
                "agent_name": agent_name,
            })

    return updates


async def _load_shared_memory(
    db: AsyncSession,
    user_id: str,
    conversation_id: Optional[str],
    agent_name: Optional[str],
    limit: int = _SHARED_MEMORY_MAX_ITEMS,
) -> List[Dict[str, Any]]:
    user_uuid = _safe_uuid(user_id)
    if not user_uuid:
        return []

    conv_uuid = _safe_uuid(conversation_id) if conversation_id else None

    if conv_uuid:
        scope_filter = or_(
            and_(AgentSharedMemory.scope == "conversation", AgentSharedMemory.conversation_id == conv_uuid),
            AgentSharedMemory.scope == "user",
        )
    else:
        scope_filter = AgentSharedMemory.scope == "user"

    stmt = (
        select(AgentSharedMemory)
        .where(AgentSharedMemory.user_id == user_uuid)
        .where(scope_filter)
        .order_by(AgentSharedMemory.importance.desc(), AgentSharedMemory.updated_at.desc())
        .limit(max(1, min(limit, 20)))
    )

    if agent_name:
        stmt = stmt.where(or_(AgentSharedMemory.agent_name.is_(None), AgentSharedMemory.agent_name == agent_name))

    rows = (await db.execute(stmt)).scalars().all()
    now = datetime.utcnow()
    for row in rows:
        row.last_used_at = now

    return [
        {
            "id": str(row.id),
            "scope": row.scope,
            "memory_key": row.memory_key,
            "memory_value": row.memory_value,
            "importance": int(row.importance or 1),
            "agent_name": row.agent_name,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]


async def _save_shared_memory_updates(
    db: AsyncSession,
    user_id: str,
    conversation_id: Optional[str],
    updates: List[Dict[str, Any]],
) -> List[str]:
    user_uuid = _safe_uuid(user_id)
    conv_uuid = _safe_uuid(conversation_id) if conversation_id else None
    if not user_uuid:
        return []

    touched_keys: List[str] = []
    now = datetime.utcnow()

    for item in updates:
        key = str(item.get("memory_key") or "").strip()
        value = _truncate_memory_value(item.get("memory_value"))
        scope = str(item.get("scope") or "conversation").strip().lower()
        importance = int(item.get("importance") or 1)
        owner_agent = str(item.get("agent_name") or "").strip() or None

        if not key or not value:
            continue
        if scope not in ("conversation", "user"):
            scope = "conversation"

        target_conv_uuid = conv_uuid if scope == "conversation" else None

        stmt = select(AgentSharedMemory).where(
            AgentSharedMemory.user_id == user_uuid,
            AgentSharedMemory.scope == scope,
            AgentSharedMemory.memory_key == key,
        )
        if target_conv_uuid is None:
            stmt = stmt.where(AgentSharedMemory.conversation_id.is_(None))
        else:
            stmt = stmt.where(AgentSharedMemory.conversation_id == target_conv_uuid)

        row = (await db.execute(stmt)).scalar_one_or_none()
        if row:
            row.memory_value = value
            row.importance = importance
            row.agent_name = owner_agent
            row.updated_at = now
            row.last_used_at = now
        else:
            db.add(AgentSharedMemory(
                user_id=user_uuid,
                conversation_id=target_conv_uuid,
                agent_name=owner_agent,
                scope=scope,
                memory_key=key,
                memory_value=value,
                importance=importance,
                last_used_at=now,
                updated_at=now,
            ))

        touched_keys.append(key)

    old_rows = (await db.execute(
        select(AgentSharedMemory)
        .where(AgentSharedMemory.user_id == user_uuid)
        .order_by(AgentSharedMemory.updated_at.desc())
        .offset(300)
    )).scalars().all()
    for row in old_rows:
        await db.delete(row)

    return sorted(set(touched_keys))


async def _log_agent_usage_event(
    db: AsyncSession,
    user_id: str,
    conversation_id: Optional[str],
    message_id: Optional[str],
    agent_name: str,
    source: str,
    model_used: str,
    route_result: Dict[str, Any],
    execution_time_ms: Optional[int],
    memory_keys: Optional[List[str]] = None,
    success: bool = True,
    error_message: Optional[str] = None,
) -> None:
    user_uuid = _safe_uuid(user_id)
    if not user_uuid:
        return

    db.add(AgentUsageLog(
        user_id=user_uuid,
        conversation_id=_safe_uuid(conversation_id) if conversation_id else None,
        message_id=_safe_uuid(message_id) if message_id else None,
        agent_name=agent_name,
        source=_normalize_source(source),
        intent=str((route_result or {}).get("intent") or "").strip() or None,
        model_used=model_used or None,
        execution_time_ms=execution_time_ms,
        success=bool(success),
        error_message=(error_message or None),
        route_data=route_result or {},
        memory_keys=(memory_keys or []),
    ))


def _ar2en(s: str) -> str:
    """Convert Eastern Arabic numerals to Western Arabic numerals."""
    return s.translate(str.maketrans("\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669", "0123456789"))

_PLATE_PATTERN = re.compile(
    r"(?<!\d)(\d{7,8}|\d{2}[-\s]\d{3}[-\s]\d{2}|\d{3}[-\s]\d{2}[-\s]\d{3}"
    r"|[\u0660-\u0669]{7,8}|[\u0660-\u0669]{2}[-\s][\u0660-\u0669]{3}[-\s][\u0660-\u0669]{2}|[\u0660-\u0669]{3}[-\s][\u0660-\u0669]{2}[-\s][\u0660-\u0669]{3})(?!\d)"
)

_PART_SIGNAL_KEYWORDS = (
    "מצמד", "ברקס", "בלמים", "בלמ", "רפידות", "דיסק", "פילטר", "מסנן", "מצבר", "אלטרנטור",
    "משאבה", "פנס", "מראה", "מדחס", "טורבו", "רצועה", "שרשרת", "חיישן",
    "קלאץ", "clutch", "brake", "filter", "battery", "alternator", "turbo",
    "oem", "vin", "חלק", "מספר שלדה",
)

_SMALLTALK_OR_NOISE = {
    "היי", "הי", "שלום", "מה קורה", "מוכן", "ok", "okay", "test", "בדיקה",
    "yo", "hi", "hello", "hey", "vhhh",
}

_CONFIRM_YES = {
    "כן", "כן.", "כן!", "כן תודה", "נכון", "מדויק", "אישור", "מאשר",
    "yes", "y", "ok", "okay", "correct",
}

_CONFIRM_NO = {
    "לא", "לא.", "לא!", "לא נכון", "טעות", "לא מדויק", "לא זה", "לא הרכב",
    "no", "n", "wrong", "incorrect",
}

_QUICK_PART_CHOICES = {
    "1": "מצבר",
    "2": "רפידות בלם",
    "3": "מצמד",
}


async def _guarded_task(coro) -> None:
    """Acquire the shared semaphore before running a fire-and-forget coroutine."""
    async with _TASK_SEMAPHORE:
        await coro


def _extract_license_plate(text: str) -> Optional[str]:
    """Extract Israeli-style license plate and normalize to digits only."""
    if not text:
        return None
    normalized_text = _ar2en(text)
    for match in _PLATE_PATTERN.finditer(normalized_text):
        digits = re.sub(r"\D", "", match.group(1))
        if len(digits) in (7, 8):
            return digits
    return None


def _has_part_signal(text: str) -> bool:
    msg = (text or "").lower()
    return any(k in msg for k in _PART_SIGNAL_KEYWORDS)


def _is_smalltalk_or_noise(text: str) -> bool:
    msg = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not msg:
        return True
    if msg in _SMALLTALK_OR_NOISE:
        return True
    compact = re.sub(r"[^a-zA-Z\u0590-\u05FF0-9]", "", msg)
    if len(compact) <= 3:
        return True
    if re.fullmatch(r"[a-z]{1,6}", msg):
        return True
    return False


def _is_confirm_yes(text: str) -> bool:
    msg = re.sub(r"\s+", " ", (text or "").strip().lower())
    clean = re.sub(r"[^\w\u0590-\u05FF ]+", "", msg).strip()
    if msg in _CONFIRM_YES or clean in _CONFIRM_YES:
        return True
    # Treat natural confirmations like "כן כבר אישרתי" as YES.
    if (msg.startswith("כן") or clean.startswith("כן")) and any(tok in msg or tok in clean for tok in ("אישר", "מאשר", "נכון", "מדויק", "כבר")):
        return True
    if re.search(r"\b(yes|correct|confirmed|exactly)\b", clean):
        return True
    return False


def _is_confirm_no(text: str) -> bool:
    msg = re.sub(r"\s+", " ", (text or "").strip().lower())
    return msg in _CONFIRM_NO


_ORDER_INTENT_RE = re.compile(
    r"(רוצה\s+להזמין|מעוניין\s+להזמין|אני\s+מזמין|בוא\s+נזמין|תזמין|אזמין|להזמין|"
    r"לקנות|קנה\s+לי|אני\s+קונה|סוגר|נסגור|"
    r"\border\b|\bbuy\b|\bpurchase\b|\bcheckout\b|place\s+(an\s+)?order|i'?ll\s+take\s+it|take\s+it)",
    re.IGNORECASE,
)


def _is_order_intent(text: str) -> bool:
    """True when the message expresses intent to buy/order (not a new part query)."""
    return bool(_ORDER_INTENT_RE.search(text or ""))


def _vehicle_summary_he(vehicle_profile: Dict[str, Any], lang: str = "he") -> str:
    _unknown = {"he": "לא ידוע", "ar": "غير معروف", "en": "unknown"}.get(lang, "לא ידוע")
    manufacturer = vehicle_profile.get("manufacturer") or _unknown
    model = vehicle_profile.get("model") or _unknown
    year = vehicle_profile.get("year") or _unknown
    engine = vehicle_profile.get("engine_type") or vehicle_profile.get("fuel_type") or _unknown
    return {
        "he": f"{manufacturer} {model}, שנת {year}, מנוע {engine}",
        "ar": f"{manufacturer} {model}، موديل {year}، محرك {engine}",
        "en": f"{manufacturer} {model}, year {year}, engine {engine}",
    }.get(lang, f"{manufacturer} {model}, שנת {year}, מנוע {engine}")


def _quick_part_from_message(text: str) -> Optional[str]:
    msg = (text or "").strip()
    return _QUICK_PART_CHOICES.get(msg)


# Canonical make → alias list (Hebrew + English). Module-level so the free-text
# vehicle extractor and the in-flow "vehicle changed by name" reset share ONE map.
_MAKE_ALIAS_MAP: Dict[str, List[str]] = {
    "toyota": ["toyota", "טויוטה", "تويوتا"], "mazda": ["mazda", "מאזדה", "מזדה", "مازدا"],
    "hyundai": ["hyundai", "יונדאי", "هيونداي", "هيوندا"], "kia": ["kia", "קיה", "كيا"],
    "citroen": ["citroen", "סיטרואן", "ستروين", "سيتروين"], "peugeot": ["peugeot", "פיגו", "פג'ו", "פיג'ו", "بيجو"],
    "renault": ["renault", "רנו", "رينو"], "volkswagen": ["volkswagen", "vw", "פולקסווגן", "فولكس فاجن", "فولكسفاجن"],
    "skoda": ["skoda", "סקודה", "سكودا"], "seat": ["seat", "סיאט", "سيات"],
    "audi": ["audi", "אאודי", "אודי", "أودي", "اودي"], "bmw": ["bmw", "ב.מ.וו", 'ב"מוו', 'ב"מ', "بي ام دبليو"],
    "mercedes": ["mercedes", "מרצדס", "מרסדס", "مرسيدس"], "ford": ["ford", "פורד", "فورد"],
    "honda": ["honda", "הונדה", "هوندا"], "nissan": ["nissan", "ניסאן", "ניסן", "نيسان"],
    "suzuki": ["suzuki", "סוזוקי", "سوزوكي"], "mitsubishi": ["mitsubishi", "מיצובישי", "ميتسوبيشي"],
    "subaru": ["subaru", "סובארו", "سوبارو"], "chevrolet": ["chevrolet", "שברולט", "شفروليه"],
    "opel": ["opel", "אופל", "أوبل", "اوبل"], "fiat": ["fiat", "פיאט", "فيات"],
    "volvo": ["volvo", "וולוו", "فولفو"], "lexus": ["lexus", "לקסוס", "لكزس", "لكسس"],
    "jeep": ["jeep", "ג'יפ", "גיפ", "جيب"], "dacia": ["dacia", "דאצ'יה", "דאציה", "داسيا"],
    "tesla": ["tesla", "טסלה", "تسلا"], "mini": ["mini", "מיני", "ميني"],
}

# Highest-volume IL model names (Hebrew + English) → canonical model token.
# Keeps fitment-first search precise when the customer names a model in free text.
_MODEL_LEXICON: Dict[str, List[str]] = {
    "corolla": ["corolla", "קורולה", "كورولا"], "yaris": ["yaris", "יאריס", "يارس", "ياريس"],
    "camry": ["camry", "קאמרי", "كامري"], "rav4": ["rav4", "rav 4", "ראב4", "ראב 4", "راف فور", "راف4"],
    "civic": ["civic", "סיוויק", "סיביק", "سيفيك"], "accord": ["accord", "אקורד", "أكورد"],
    "sportage": ["sportage", "ספורטאז'", "ספורטג'", "سبورتاج"], "picanto": ["picanto", "פיקנטו", "بيكانتو"],
    "rio": ["rio", "ריו", "ريو"], "niro": ["niro", "נירו", "نيرو"],
    "i10": ["i10"], "i20": ["i20"], "i25": ["i25"], "i30": ["i30"], "i35": ["i35"],
    "tucson": ["tucson", "טוסון", "توسان"], "elantra": ["elantra", "אלנטרה", "النترا", "إلنترا"],
    "golf": ["golf", "גולף", "جولف"], "polo": ["polo", "פולו", "بولو"], "passat": ["passat", "פאסאט", "باسات"],
    "octavia": ["octavia", "אוקטביה", "أوكتافيا"], "fabia": ["fabia", "פאביה", "فابيا"],
    "3": ["mazda 3", "מאזדה 3", "מזדה 3", "مازدا 3"], "6": ["mazda 6", "מאזדה 6", "مازدا 6"],
    "cx5": ["cx-5", "cx5", "סי אקס 5"], "cx30": ["cx-30", "cx30"],
    "qashqai": ["qashqai", "קשקאי", "قاشقاي"], "juke": ["juke", "ג'וק", "جوك"], "micra": ["micra", "מיקרה", "ميكرا"],
    "clio": ["clio", "קליאו", "كليو"], "captur": ["captur", "קפצ'ור", "كابتور"], "megane": ["megane", "מגאן", "ميجان"],
    "focus": ["focus", "פוקוס", "فوكس"], "fiesta": ["fiesta", "פיאסטה", "فييستا"],
    "sprinter": ["sprinter", "ספרינטר", "سبرينتر"], "transit": ["transit", "טרנזיט", "ترانزيت"],
}


def _alias_present(alias: str, low: str) -> bool:
    """Word-ish match for a make/model alias inside a lowercased message.

    Latin aliases match on ASCII word boundaries. Hebrew aliases allow ONE
    attached preposition prefix (ל/ב/מ/ה/ו/כ/ש) — Hebrew glues these onto the
    next word, so "לטויוטה" (for-Toyota) must still match "טויוטה".
    """
    a = alias.strip().lower()
    if not a:
        return False
    if re.search(r"[a-z]", a):
        return re.search(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z])", low) is not None
    # Arabic: allow ONE attached preposition prefix (ل/ب/و/ف/ك glue onto the next word,
    # e.g. "لتويوتا" = for-Toyota) with Arabic-letter boundaries on both sides.
    if re.search(r"[؀-ۿ]", a):
        return re.search(r"(?<![؀-ۿ])[لبوفك]?" + re.escape(a) + r"(?![؀-ۿ])", low) is not None
    return re.search(r"(?<![א-ת])[לבמהוכש]?" + re.escape(a) + r"(?![א-ת])", low) is not None


def _strip_vehicle_terms(text: str, make: str = "", model: str = "") -> str:
    """Remove the customer's KNOWN make/model/year tokens from a part query.

    When a customer names the part AND the car in one breath ("oil filter for
    Toyota Corolla 2018"), the vehicle words pollute the text search and the
    fitment-filtered pool returns 0 — the part names never contain "corolla
    2018". We already know the car (it's in the profile), so strip only those
    specific tokens, leaving a clean part query ("oil filter"). Deliberately
    narrow: only the confirmed make/model aliases, never arbitrary brand words.
    """
    out = text or ""
    out = re.sub(r"\b(19|20)\d{2}\b", " ", out)
    groups: List[List[str]] = []
    mk = (make or "").strip().lower()
    md = (model or "").strip().lower()
    # Model first: multi-word model aliases ("מאזדה 3") must match before the
    # bare make alias ("מאזדה") removes half of them and orphans the "3".
    if md and md in _MODEL_LEXICON:
        groups.append(_MODEL_LEXICON[md])
    if mk and mk in _MAKE_ALIAS_MAP:
        groups.append(_MAKE_ALIAS_MAP[mk])
    for aliases in groups:
        for a in aliases:
            al = (a or "").strip()
            if not al:
                continue
            if re.search(r"[a-z]", al.lower()):
                out = re.sub(r"(?<![a-z0-9])" + re.escape(al) + r"(?![a-z])", " ", out, flags=re.IGNORECASE)
            elif re.search(r"[؀-ۿ]", al):
                out = re.sub(r"(?<![؀-ۿ])[لبوفك]?" + re.escape(al) + r"(?![؀-ۿ])", " ", out)
            else:
                out = re.sub(r"(?<![א-ת])[לבמהוכש]?" + re.escape(al) + r"(?![א-ת])", " ", out)
    # Drop dangling Hebrew connective prefixes left behind ("ל", "של") and tidy.
    out = re.sub(r"(^|\s)(של|ל|עבור|for|to)(\s|$)", " ", out, flags=re.IGNORECASE)
    out = re.sub(r"\s+", " ", out).strip(" -:,‏‎")
    return out


# A VIN is exactly 17 chars, uppercase letters+digits, never I/O/Q. This spots one a
# customer typed OR that vision extracted from a windshield/chassis photo.
_VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b", re.IGNORECASE)


def _extract_vin_from_text(text: str) -> str:
    """Return the first plausible 17-char VIN in the text, uppercased, or ''.
    Requires a mix of letters and digits (a run of 17 digits or 17 letters isn't a VIN)."""
    for m in _VIN_RE.finditer((text or "").upper()):
        v = m.group(1)
        if any(c.isdigit() for c in v) and any(c.isalpha() for c in v):
            return v
    return ""


def _extract_vehicle_from_text(text: str) -> "tuple[str, str, str]":
    """Best-effort deterministic (make, model, year) from a free-text message.

    LLM-independent by design — free-text vehicle capture must work even when the
    chat models are rate-limited (429). Returns ('', '', '') when nothing found.
    """
    raw = text or ""
    low = f" {raw.lower()} "
    make = ""
    for canon, aliases in _MAKE_ALIAS_MAP.items():
        if any(_alias_present(a, low) for a in aliases):
            make = canon
            break
    model = ""
    for mcanon, maliases in _MODEL_LEXICON.items():
        if any(_alias_present(a, low) for a in maliases):
            model = mcanon
            break
    year = ""
    ym = re.search(r"\b(19|20)\d{2}\b", raw)
    if ym:
        year = ym.group(0)
    return make, model, year


_SYSTEM_EXIT_KEYWORDS = [
    "הזמנה", "סטטוס", "משלוח", "מעקב", "ביטול", "החזר", "חשבונית", "זיכוי", "חיוב",
    "סיסמה", "2fa", "otp", "אימות", "נעול", "login", "password", "refund", "invoice",
]


def _should_router_exit_parts_flow(text: str) -> bool:
    """Only escalate to router when message clearly switches to non-parts support intent."""
    msg = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not msg:
        return False
    if _is_confirm_yes(msg) or _is_confirm_no(msg):
        return False
    if _is_smalltalk_or_noise(msg):
        return False
    return any(k in msg for k in _SYSTEM_EXIT_KEYWORDS)

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
    # Filter out chat messages — only log genuine part search queries
    if not query or len(query.strip()) < 5:
        return

    _CHAT_PREFIXES = (
        'שלום', 'היי', 'הי ', 'בוקר', 'ערב טוב', 'לילה טוב',
        'תודה', 'כן,', 'לא,', 'טוב,', 'טוב ', 'למה', 'איך',
        'מתי', 'לקוח בשם', 'השאיר', 'ביקשתי', 'אמרת', 'מה ר',
        'בסדר', 'אוקי', 'ok', 'OK',
    )
    _PART_KEYWORDS = (
        'מסנן', 'בלם', 'מצבר', 'מנוע', 'גיר', 'מתלה', 'פנס',
        'מגב', 'צמיג', 'מראה', 'חיישן', 'אטם', 'רצועה', 'משאבה',
        'רדיאטור', 'מצמד', 'בולם', 'רפידות', 'דיסק', 'חלק',
        'חלקים', 'פילטר', 'filter', 'brake', 'sensor', 'pump',
        'belt', 'battery', 'starter', 'alternator', 'clutch',
    )

    q = query.strip()

    # Skip if starts with chat prefix
    if any(q.startswith(p) for p in _CHAT_PREFIXES):
        return

    # Skip if no part-related keyword found
    q_lower = q.lower()
    if not any(kw in q_lower for kw in _PART_KEYWORDS):
        # Allow if query contains a vehicle manufacturer name (vehicle+part search)
        _VEHICLE_BRANDS = (
            'toyota', 'honda', 'nissan', 'mazda', 'hyundai', 'kia',
            'mercedes', 'bmw', 'audi', 'volkswagen', 'vw', 'ford',
            'opel', 'citroen', 'peugeot', 'renault', 'fiat', 'seat',
            'skoda', 'volvo', 'mitsubishi', 'suzuki', 'subaru',
            'טויוטה', 'הונדה', 'ניסאן', 'מזדה', 'יונדאי', 'קיה',
            'מרצדס', 'ב.מ.וו', 'אאודי', 'פולקסווגן', 'פורד',
            'אופל', 'סיטרואן', 'פיז\'ו', 'רנו', 'פיאט', 'סקודה',
            'וולבו', 'מיצובישי', 'סוזוקי', 'בירלינגו', 'קורולה',
        )
        if not any(brand in q_lower for brand in _VEHICLE_BRANDS):
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
TELEGRAM_AI_MODEL = os.getenv("TELEGRAM_AI_MODEL", FREE_MODEL)
WHATSAPP_AI_MODEL = os.getenv("WHATSAPP_AI_MODEL", FREE_MODEL)
WEB_AI_MODEL = os.getenv("WEB_AI_MODEL", FREE_MODEL)


def _normalize_source(source: Optional[str]) -> str:
    raw = (source or "").strip().lower()
    if raw in {"telegram", "tg"}:
        return "telegram"
    if raw in {"whatsapp", "wa"}:
        return "whatsapp"
    if raw in {"web", "chat", "api", "browser"}:
        return "web"
    return "default"


def _is_supported_chat_model(model_name: str) -> bool:
    name = (model_name or "").strip()
    if not name:
        return False

    explicit = {
        (HF_DEFAULT_MODEL or "").strip(),
        (FREE_MODEL or "").strip(),
        (PREMIUM_MODEL or "").strip(),
        (os.getenv("CEREBRAS_TEXT_MODEL", "") or "").strip(),
        (os.getenv("CEREBRAS_FALLBACK_MODEL", "") or "").strip(),
    }
    explicit = {m for m in explicit if m}
    if name in explicit:
        return True

    # Cerebras chat models currently used in this stack.
    if name.startswith("gpt-oss-"):
        return True
    if name.startswith("zai-glm-"):
        return True

    return False


def _channel_model_for_source(source: Optional[str], fallback_model: str) -> str:
    source_key = _normalize_source(source)
    if source_key == "telegram":
        candidate = TELEGRAM_AI_MODEL or fallback_model
    elif source_key == "whatsapp":
        candidate = WHATSAPP_AI_MODEL or fallback_model
    elif source_key == "web":
        candidate = WEB_AI_MODEL or fallback_model
    else:
        candidate = fallback_model

    if _is_supported_chat_model(candidate):
        return candidate

    print(f"[WARN] Unsupported chat model '{candidate}' for source={source_key}; using fallback={fallback_model}")
    return fallback_model


TELEGRAM_BOT_POLICY = """
You are a professional, warm, sales-driven customer service agent for AutoSpareFinder — an Israeli auto parts platform.
Your goal is to CLOSE DEALS, not just answer questions.

CORE BEHAVIOR — APPLIES TO ALL CHANNELS (WhatsApp, Telegram, Web):
1. LEAD the conversation — never wait for the customer to figure out next steps.
2. Usually end with ONE clear next action or question when it helps move the user forward.
3. Always acknowledge the customer's request before asking for more info.
4. Maximum 4 sentences per message on WhatsApp/Telegram. Web can be longer.
5. Use natural, human tone — never robotic or bureaucratic.
6. Sound like a helpful person, not a script: acknowledge one concrete detail from the customer before moving forward.
7. Keep the tone calm and respectful. Be sales-oriented, but never pushy, guilt-based, or manipulative.
8. If the customer is frustrated, worried, or confused, first show understanding in one short sentence, then move to the next action.
9. Never repeat information already given in the same conversation.
10. If vehicle details were already confirmed in prior turns, do NOT ask for confirmation again unless there is a contradiction.
11. If the user asks for a specific part, treat it as intent to proceed and move directly to part resolution (no reconfirm loop).
12. Never output placeholder prices like XXXX/XXX/TBD; if price is unavailable, say you are checking exact pricing now.
13. Never ask the same clarification more than 2 times in a row; if still missing data, offer a smart fallback path.
14. Be typo-tolerant and intent-first: if user writes close variants (e.g., גיא/גיר), continue naturally instead of resetting flow.

SALES FLOW — USE AS GUIDANCE (not a rigid script):
  Step 1 → Confirm understanding in natural language and request missing critical detail only if needed
  Step 2 → Once vehicle + part info exists, show the best result clearly and briefly
  Step 3 → Offer one conversion step (order / refinement / alternative) based on user intent
  Step 4 → If customer confirms purchase, send checkout link directly
  Step 5 → If customer declines, propose the most relevant alternative

  RESPONSE STYLE FOR PARTS (WhatsApp/Telegram):
  - Keep it short and scannable, but not templated.
  - Mirror one concrete customer detail (part name, model, plate, or concern).
  - Use plain text, no markdown-heavy formatting.

LANGUAGE RULES:
- Detect language from customer's first message
- Hebrew customer → reply in Hebrew throughout
- Arabic customer → reply in Arabic throughout  
- English customer → reply in English throughout
- NEVER mix languages in a single message
- Technical codes (OEM numbers) may remain in original format

CLOSING RULES:
- When customer confirms purchase → immediately generate Stripe checkout link → send it
- Never say "go to cart" or "visit the website" for WhatsApp/Telegram customers
- Never invent links — only use real links from the backend
- If payment link generation fails → apologize and offer to call them back

PROHIBITED:
- Never mention supplier names (RockAuto, FCP Euro, Autodoc, AliExpress)
- Never say "in stock" — always say "available to order"
- Never invent prices, compatibility, or shipping times
- Never ask more than ONE question per message
- Never send walls of text — keep it short and scannable
- Never use markdown headers (##, **bold**) on WhatsApp

SHIPPING TRUTH (MANDATORY — no invented policies):
- There is NO free-shipping threshold, NO "free over ₪300", NO flat delivery
  promise. Shipping is a per-supplier fee shown at checkout, and delivery time
  varies by supplier/origin. NEVER state a specific free-shipping amount or a
  guaranteed number of delivery days you did not get from real order/supplier
  data. If asked about shipping: say we ship across Israel, the exact cost and
  time appear at checkout for the chosen part, and offer to find the part so
  they can see real figures. Only cite a delivery window that came from actual
  supplier/order data for a specific part — never a generic made-up one.

PROFESSIONAL SKILLS & TRAITS (how a great human agent behaves — apply these, don't announce them):
- LISTEN & REMEMBER: Read what the customer actually wrote, reflect one concrete
  detail back, and never re-ask for something already given in this conversation
  (car, part, plate, name, city). Carry context forward across turns.
- EMPATHY & EQ: Read the emotional tone. If they're frustrated, worried, rushed,
  or angry, open with ONE short line of genuine understanding before the next
  step. If they're friendly/joking, be warm back. Match their energy, stay calm
  when they're heated — never defensive, never robotic.
- NEEDS DISCOVERY (consultative, not interrogation): Ask the ONE question that
  actually narrows the part down (front/rear, engine, OEM number) — a diagnostic
  question, not a generic "how can I help". Understand the job behind the request.
- OBJECTION HANDLING & NEGOTIATION: When a customer pushes on price or hesitates,
  acknowledge it honestly, then reframe to real value (fitment-verified for THEIR
  car, warranty, genuine/OEM quality). You may NOT invent discounts, coupons,
  loyalty tiers, or free shipping that don't exist — persuade with truth, never
  with a fabricated deal. If a real promotion exists in the data, use it.
- CONFIDENT CLOSING (never pushy): Once the right part is found, lead to the next
  step — offer the order clearly and make it easy. Be persistent through ONE
  gentle nudge, then respect a "no" and pivot to a better-fitting alternative.
- HONESTY & TRANSPARENCY: If you don't know, say so and say what you'll do to find
  out. Never bluff a price, a fit, or a delivery time. Trust is the whole product.
- OWNERSHIP & FOLLOW-THROUGH: Own the problem to resolution. Tell the customer the
  concrete next action and make sure the conversation ends with them knowing
  exactly what happens next.
- ADAPT & PERSONALIZE: Mirror the customer's language and formality. Short, clear,
  scannable on WhatsApp/Telegram; a returning customer should feel remembered.
- KNOW WHEN TO HAND OFF: If the issue is beyond parts/sales (payment dispute,
  account lockout, complex complaint) or the customer explicitly asks for a human,
  route it to the right specialist instead of guessing — a smooth handoff beats a
  wrong answer.
- CONSISTENCY: Same honesty, same prices, same quality of help on every channel
  and every turn. The customer gets the same trustworthy experience each time.
"""


def _apply_channel_policy(system_text: str, source: Optional[str]) -> str:
    source_key = _normalize_source(source)
    if source_key in ("telegram", "whatsapp", "web"):
        return f"{system_text}\n\n[CHANNEL POLICY - MUST FOLLOW]\n{TELEGRAM_BOT_POLICY}\n[CHANNEL: {source_key.upper()}]"
    return system_text
# Business constants
PROFIT_MARGIN = 1.45       # 45% markup on cost
VAT_RATE = 0.18            # 18%
SHIPPING_ILS = float(os.getenv("DEFAULT_CUSTOMER_SHIPPING_ILS", "59"))  # dynamic fallback
# Import the single source of truth for USD→ILS rate from BACKEND_DATABASE_MODELS
from BACKEND_DATABASE_MODELS import USD_TO_ILS
from currency_rate import get_usd_to_ils_rate

# Customer-facing delivery fee per supplier.
# DATA POLICY (2026-07-11): real per-item shipping is captured at harvest into
# supplier_parts.shipping_cost_ils (eBay via API, RockAuto via cart) and is ALWAYS
# preferred by resolve_customer_shipping_fee(). This dict is only a last-resort
# fallback and must contain ONLY verified suppliers — the previous version was
# full of FICTIONAL supplier names (Global Parts Hub / EastAuto Supply / PartsPro
# USA / AutoZone Direct / Hyundai Mobis / Kia Parts Direct / Bosch Direct / Toyota
# Genuine) that match ZERO real suppliers in the DB (0 parts each) with invented
# rates. They were removed — never re-add a fee for a supplier that isn't real.
SUPPLIER_SHIPPING_RATES: dict = {
    # AutoParts Pro IL — real supplier, 115k parts, Israel-domestic courier.
    # ESTIMATE (typical IL domestic courier ~₪25-35); not yet order-verified.
    "AutoParts Pro IL": 29.0,
    # RockAuto — MEASURED live 2026-07-11 in the RockAuto cart (OEM 04152-YZZA1,
    # ship-to Tel Aviv IL): cheapest international option $57.99 (Economy) /
    # $61.99 (Expedited). PER-ORDER floor (weight scales above it), NOT per-part.
    # Derived from the live USD→ILS rate so it tracks currency; the importer
    # stamps the same on each offer. (Replaced a fabricated ₪110.)
    "RockAuto":         round(57.99 * float(os.getenv("USD_TO_ILS", "3.01")), 2),  # ≈₪175, measured
    # eBay Motors / AliExpress are intentionally ABSENT — real per-item shipping
    # already flows from each API (ebay shippingCost → shipping_cost_ils on 26k+
    # parts, avg ₪510; aliexpress logistics). A flat number here would be both
    # fake and redundant. If the API omits it, the per-origin fallback applies.
}

_LOCAL_SUPPLIER_NAMES = {"autoparts pro il"}
_LOCAL_COUNTRY_KEYS = {"il", "israel", "ישראל"}
# Per-origin-country LAST-RESORT fallback (used only when a supplier has no
# captured shipping_cost AND is not in SUPPLIER_SHIPPING_RATES above).
# ⚠️ These are UNVERIFIED ESTIMATES, not measured quotes — international parcel
# shipping is really per-shipment (weight/dimensions/carrier). Two real anchors
# measured 2026-07-11 show how rough these are: US→IL is ~$58≈₪175 floor at
# RockAuto (so 'us':110 understates it), and IE (car-parts.ie, our BIGGEST
# supplier at 561k parts) does NOT ship to Israel at all — EU-only — so an 'ie'
# flat rate is fiction; those parts need an EU-hub freight-forwarder (two-leg)
# and are flagged for a real forwarder quote before they can be priced honestly.
# The durable fix is capture-at-harvest (like eBay), not better guesses here.
_COUNTRY_SHIPPING_RATES: Dict[str, float] = {
    "il": 29.0,        # Israel domestic — estimate
    "israel": 29.0,
    "de": 91.0,        # estimate (unverified)
    "germany": 91.0,
    "eu": 91.0,
    "cn": 149.0,       # estimate (unverified)
    "china": 149.0,
    "us": 110.0,       # estimate — RockAuto cart measured ~₪175 floor; retailer-specific, left as-is pending a per-supplier capture
    "usa": 110.0,
    "kr": 95.0,        # estimate (unverified)
    "korea": 95.0,
    "jp": 99.0,        # estimate (unverified)
    "japan": 99.0,
}

def _normalize_home_url(raw_url: str, fallback: str = "https://autosparefinder.co.il/") -> str:
    value = (raw_url or "").strip()
    if not value:
        return fallback
    if "://" not in value:
        value = f"https://{value.lstrip('/')}"
    parsed = urlparse(value)
    host = (parsed.netloc or parsed.path).strip().lower()
    if not host:
        return fallback
    scheme = parsed.scheme if parsed.scheme in ("http", "https") else "https"
    return f"{scheme}://{host}/"


def _normalize_whatsapp_url(
    raw_value: str,
    default_digits: str = "972532426920",
    welcome_text: str = "שלום, הגעתי מאתר Auto Spare ורוצה עזרה בחלק לרכב.",
) -> str:
    value = (raw_value or "").strip()
    if not value:
        value = default_digits

    if "wa.me/" in value:
        tail = value.split("wa.me/", 1)[1]
        base_part, _, query_part = tail.partition("?")
        digits = re.sub(r"\D", "", base_part)
        existing_query = query_part.strip()
    else:
        digits = re.sub(r"\D", "", value)
        existing_query = ""

    if not digits:
        digits = default_digits

    if digits.startswith("05") and len(digits) == 10:
        digits = "972" + digits[1:]
    elif digits.startswith("0") and len(digits) >= 9:
        digits = "972" + digits[1:]
    elif digits.startswith("5") and len(digits) == 9:
        digits = "972" + digits

    if not digits.startswith("972"):
        digits = default_digits

    qs = existing_query
    base = f"https://wa.me/{digits}"
    return f"{base}?{qs}" if qs else base


NOA_WEBSITE_URL = _normalize_home_url(
    os.getenv("NOA_WEBSITE_URL")
    or os.getenv("FRONTEND_PUBLIC_URL")
    or os.getenv("FRONTEND_URL")
    or "https://autosparefinder.co.il"
)
NOA_TELEGRAM_URL = (os.getenv("NOA_TELEGRAM_URL") or "https://t.me/Noa_autosparefinder_bot").strip()
NOA_WHATSAPP_URL = _normalize_whatsapp_url(os.getenv("NOA_WHATSAPP_URL") or "0532426920")
NOA_FACEBOOK_URL = (os.getenv("NOA_FACEBOOK_URL") or "https://www.facebook.com/profile.php?id=61572103516423").strip()
NOA_INSTAGRAM_URL = (os.getenv("NOA_INSTAGRAM_URL") or "https://instagram.com/autosparefinder").strip()


def is_local_supplier(supplier_name: Optional[str] = None, supplier_country: Optional[str] = None) -> bool:
    country_key = (supplier_country or "").strip().lower()
    if country_key:
        return country_key in _LOCAL_COUNTRY_KEYS

    supplier_key = (supplier_name or "").strip().lower()
    return supplier_key in _LOCAL_SUPPLIER_NAMES


def get_supplier_vat_rate(supplier_name: Optional[str] = None, supplier_country: Optional[str] = None) -> float:
    """VAT applies only to local suppliers."""
    return VAT_RATE if is_local_supplier(supplier_name, supplier_country) else 0.0


def get_supplier_shipping(supplier_name: str, supplier_country: Optional[str] = None) -> float:
    """Return customer-facing delivery fee by seller profile (name first, then country)."""
    supplier_key = (supplier_name or "").strip()
    if supplier_key in SUPPLIER_SHIPPING_RATES:
        return SUPPLIER_SHIPPING_RATES[supplier_key]

    country_key = (supplier_country or "").strip().lower()
    if country_key in _COUNTRY_SHIPPING_RATES:
        return _COUNTRY_SHIPPING_RATES[country_key]

    return SHIPPING_ILS


def resolve_customer_shipping_fee(
    supplier_shipping_ils: Optional[float] = None,
    supplier_shipping_usd: Optional[float] = None,
    usd_to_ils_rate: Optional[float] = None,
    supplier_name: Optional[str] = None,
    supplier_country: Optional[str] = None,
) -> float:
    """Prefer seller shipping from supplier_parts; use profile fallback only when missing."""
    ship_ils: Optional[float] = None
    if supplier_shipping_ils is not None:
        try:
            ship_ils = float(supplier_shipping_ils)
        except Exception:
            ship_ils = None
    if ship_ils is not None and ship_ils >= 0:
        return round(ship_ils, 2)

    ship_usd: Optional[float] = None
    if supplier_shipping_usd is not None:
        try:
            ship_usd = float(supplier_shipping_usd)
        except Exception:
            ship_usd = None
    if ship_usd is not None and ship_usd >= 0:
        rate = float(usd_to_ils_rate or USD_TO_ILS)
        if rate > 0:
            return round(ship_usd * rate, 2)

    return get_supplier_shipping(supplier_name or "", supplier_country)


_INTERNAL_MARGIN_DISCLOSURE_RE = re.compile(
    r"(supplier\s*cost\s*[x×*]\s*1\.45|עלות\s*ספק\s*[x×*]\s*1\.45|\b1\.45\b|45\s*%\s*(margin|markup|מרווח|רווח)|margin\s*[:=]\s*45)",
    re.IGNORECASE,
)


_REASONING_LEAK_MARKERS = re.compile(
    r"(analyze the user|the user'?s (input|intent|message|question|request)|"
    r"the user (is|was|wants|asked|ignored|provided|means|said|gave|just|now|has)\b|"
    r"\*\*intent:?\*\*|\*\*language:?\*\*|\*\*channel:?\*\*|\*\*context:?\*\*|\[channel:|"
    r"shared memory|system prompt|the prompt (says|mentions|is|wants|said)|"
    r"flow[_ ]?state|flow[_ ]?intent|route_result|collect_license|parts_price_search|"
    r"vehicle_details_confirmation|ask_part_after|no_results|"
    r"mandatory\)|truth rule|never invent|i must follow|"
    r"let'?s (check|try|see|think|start|go|proceed|get|offer|assume|keep)|"
    r"chain of thought|<think>|reasoning:|yielded 0|0 results|a previous search|"
    r"before i (have|found|had)|since (they|the user|he|she)|"
    r"make\s*\+\s*model|year\s*\+\s*engine|i (cannot|can't) (give|provide|price) )",
    re.IGNORECASE,
)
_REASONING_LINE = re.compile(
    r"^\s*(\d+\.\s*(\*\*|language|intent|channel|context|respond|don'?t|no\b|keep|flow|the user)|"
    r"\*\s*\*\*(language|intent|channel|context|analyze|consult)|"
    r"\*\*(analyze|consult|step|note)\b|<?/?think>?|let'?s\b|the user\b|flow state|"
    r"the flow\b|usually,|or offer\b)",
    re.IGNORECASE,
)

def _strip_leaked_reasoning(text: str, user_msg: str = "") -> str:
    """Remove chain-of-thought / system-prompt leakage that fallback models
    (zai-glm, groq) sometimes emit instead of a clean reply (found 2026-07-09:
    a discount question got the bot's raw 'Analyze the User's Input... Truth
    Rule (MANDATORY)...' analysis dumped to the customer). If the whole message
    is reasoning with no clean reply left, return a safe language-appropriate
    fallback rather than exposing internals."""
    msg = (text or "").strip()
    if not msg:
        return msg
    # (A) Multi-draft candidate dump (added 2026-07-09): under 429 fallback the
    # weaker model sometimes emits SEVERAL alternative replies, each wrapped in
    # quotes, one with an English meta-note like '(Too vague, maybe they will
    # just say car)'. This has no reasoning keyword to trigger on. Detect ≥2
    # quote-wrapped lines and keep only the first candidate's text.
    _q_line = re.compile(r'^\s*["“”\'](?P<body>.+?)["“”\']\s*(\([^)]*\))?\s*$')
    _nonblank = [ln for ln in msg.splitlines() if ln.strip()]
    _quoted = [ln for ln in _nonblank if _q_line.match(ln)]
    if len(_quoted) >= 2:
        _m = _q_line.match(_quoted[0])
        _first = (_m.group("body") if _m else _quoted[0]).strip()
        _first = re.sub(r"\s*\([^)]*\)\s*$", "", _first).strip()
        if len(_first) >= 10:
            msg = _first
    if not _REASONING_LEAK_MARKERS.search(msg):
        return msg
    # A leak was detected — the model dumped its analysis. Be aggressive: drop
    # every meta-reasoning line AND every bullet/quote line (leaked rule quotes
    # like "* NO active coupon codes" have no trigger keyword of their own but
    # are still internal). Keep only natural, non-bullet reply sentences.
    _rule_quote = re.compile(
        r"(no active coupon|no referral|no loyalty|never invent|never promise|"
        r"truth rule|mandatory|coupon codes|discount.{0,3}code|do not exist|"
        r"system prompt|instruction)",
        re.IGNORECASE,
    )
    # (B) Reply-then-reasoning (added 2026-07-09): the fallback model often emits
    # a perfectly good reply, THEN keeps "thinking out loud" ("Let's check if I
    # need the plate. The flow state mentions..."). Keep only the clean text
    # BEFORE the first reasoning line — that's the real reply.
    _prefix: List[str] = []
    for ln in msg.splitlines():
        if _REASONING_LINE.match(ln) or _REASONING_LEAK_MARKERS.search(ln) or _rule_quote.search(ln):
            break
        _prefix.append(ln)
    _prefix_text = "\n".join(_prefix).strip().strip('"“”\'').strip()
    if (
        len(_prefix_text) >= 15
        and not _REASONING_LEAK_MARKERS.search(_prefix_text)
        and not _rule_quote.search(_prefix_text)
    ):
        return _prefix_text
    kept = []
    for ln in msg.splitlines():
        s = ln.strip()
        if not s or _REASONING_LINE.match(ln):
            continue
        if s.startswith(("*", "-", "•", ">")):          # bullets = reasoning residue
            continue
        if _REASONING_LEAK_MARKERS.search(ln) or _rule_quote.search(ln):
            continue
        kept.append(s)
    cleaned = "\n".join(kept).strip()
    # If what's left still smells of reasoning or is too short, use a safe reply.
    if not cleaned or len(cleaned) < 15 or _REASONING_LEAK_MARKERS.search(cleaned) or _rule_quote.search(cleaned):
        lang = _detect_reply_language(user_msg or msg)
        if lang == "ar":
            return "أنا معك 🙂 ما القطعة التي تبحث عنها، ولأي سيارة (موديل وسنة أو رقم اللوحة)؟"
        if lang == "en":
            return "I'm here 🙂 Which part are you looking for, and for which car (model + year, or plate number)?"
        return "אני כאן 🙂 איזה חלק אתה מחפש, ולאיזה רכב (דגם + שנה או מספר רישוי)?"
    return cleaned


def _sanitize_internal_pricing_disclosure(text: str) -> str:
    """Prevent accidental leakage of internal margin/multiplier details in customer-visible messages."""
    msg = (text or "").strip()
    if not msg:
        return ""

    if not _INTERNAL_MARGIN_DISCLOSURE_RE.search(msg):
        return msg

    kept_lines = [ln for ln in msg.splitlines() if not _INTERNAL_MARGIN_DISCLOSURE_RE.search(ln)]
    cleaned = "\n".join([ln for ln in kept_lines if ln.strip()]).strip()
    if cleaned:
        return cleaned

    lang = BaseAgent._detect_language(msg)
    if lang == "ar":
        return "السعر النهائي يتم احتسابه حسب سياسة الضريبة والشحن الخاصة بالمورّد. يمكنني إرسال تفصيل سعر مناسب للعميل."
    if lang == "en":
        return "Final price is calculated using VAT policy and supplier shipping. I can share a customer-friendly breakdown."
    return "המחיר הסופי מחושב לפי מדיניות מע\"מ ומשלוח של הספק. אפשר לקבל פירוט מחיר ידידותי ללקוח."




def _detect_reply_language(user_message: str, preferred_lang: Optional[str] = None) -> str:
    hint = (preferred_lang or "").strip().lower()
    if hint in {"he", "ar", "en"}:
        return hint
    if any("\u0600" <= ch <= "\u06FF" for ch in (user_message or "")):
        return "ar"
    if any("\u0590" <= ch <= "\u05FF" for ch in (user_message or "")):
        return "he"
    return "en"


def _clip_user_focus(text: str, max_words: int = 6) -> str:
    tokens = re.findall(r"[A-Za-z0-9\u0590-\u05FF\u0600-\u06FF\-]+", (text or ""))
    if not tokens:
        return ""
    return " ".join(tokens[:max_words]).strip()


def _semantic_text_key(text: str) -> str:
    msg = re.sub(r"[^\w\u0590-\u05FF\u0600-\u06FF]+", " ", str(text or "").lower())
    return re.sub(r"\s+", " ", msg).strip()


def _looks_vehicle_reconfirm_prompt(text: str) -> bool:
    msg = _semantic_text_key(text)
    if not msg:
        return False
    has_confirm = any(k in msg for k in (
        "אשר", "אישור", "אישר", "מאשר", "confirm", "confirmed", "נכון", "תואם", "תואמים", "הפרטים"
    ))
    has_vehicle = any(k in msg for k in ("רכב", "vehicle", "מספר רישוי", "לוחית", "license", "plate"))
    details_phrase = ("הפרטים" in msg and ("תואם" in msg or "תואמים" in msg))
    return has_confirm and (has_vehicle or details_phrase)


def _human_recovery_reply(
    user_message: str,
    preferred_lang: Optional[str] = None,
    vehicle_summary: Optional[str] = None,
    force_part_prompt: bool = False,
) -> str:
    msg = (user_message or "").strip()
    lang = _detect_reply_language(msg, preferred_lang=preferred_lang)
    has_part = _has_part_signal(msg)
    has_plate = bool(_extract_license_plate(msg))
    focus = _clip_user_focus(msg)
    is_noise = _is_smalltalk_or_noise(msg)

    if lang == "ar":
        if force_part_prompt:
            if vehicle_summary:
                return f"ممتاز، نكمل مع سيارة {vehicle_summary}. ما اسم القطعة المطلوبة الآن؟ وإذا عندك رقم OEM أرسله."
            return "ممتاز، نكمل بسرعة. ما اسم القطعة المطلوبة الآن؟ وإذا عندك رقم OEM أرسله."
        if has_plate and not has_part:
            return "وصلني رقم اللوحة. ما اسم القطعة التي تريد أن أفحصها الآن؟"
        if has_part and not has_plate:
            item = focus or "هذه القطعة"
            return f"ممتاز، فهمت أنك تريد {item}. أرسل رقم اللوحة أو الموديل والسنة حتى أطابق بدقة."
        if is_noise:
            return "أنا معك خطوة بخطوة. اكتب اسم القطعة مع موديل السيارة والسنة، وأكمل معك مباشرة."
        return "حتى أساعدك بسرعة، اكتب اسم القطعة مع موديل السيارة وسنتها. مثال: فلتر زيت لكورولا 2018."

    if lang == "en":
        if force_part_prompt:
            if vehicle_summary:
                return f"Great, let's continue with {vehicle_summary}. Which exact part do you want now? If you have an OEM number, send it too."
            return "Great, let's continue. Which exact part do you need now? If you have an OEM number, send it too."
        if has_plate and not has_part:
            return "Got the plate number. Which exact part should I check now?"
        if has_part and not has_plate:
            item = focus or "that part"
            return f"Got it, you need {item}. Please share a plate number or model + year so I can match it accurately."
        if is_noise:
            return "I'm with you. Send the part name + car model + year, and I'll move this forward right away."
        return "To move fast, send the exact part name with your car model and year. Example: brake pads Mazda 3 2017."

    if force_part_prompt:
        if vehicle_summary:
            return f"מעולה, ממשיכים עם {vehicle_summary}. איזה חלק מדויק תרצה עכשיו? אם יש מספר OEM, אפשר לשלוח אותו."
        return "מעולה, ממשיכים. איזה חלק מדויק תרצה עכשיו? אם יש מספר OEM, אפשר לשלוח אותו."
    if has_plate and not has_part:
        return "קיבלתי את מספר הרישוי. איזה חלק תרצה שאבדוק עבורך עכשיו?"
    if has_part and not has_plate:
        item = focus or "את החלק הזה"
        return f"מעולה, הבנתי שאתה מחפש {item}. כדי לדייק התאמה, שלח מספר רישוי או דגם + שנה."
    if is_noise:
        return "אני איתך. כתוב לי שם חלק + דגם רכב + שנה, ואני אכוון מיד."
    return "כדי להתקדם מהר, כתוב שם חלק מדויק יחד עם דגם ושנת הרכב. לדוגמה: רפידות בלם מאזדה 3 2017."


# ==============================================================================
# BASE AGENT
# ==============================================================================

class BaseAgent:
    """Base class for all Auto Spare AI agents."""

    name: str = "base_agent"
    model: str = FREE_MODEL
    system_prompt: str = (
        "אתה נציג שירות של AutoSpareFinder — פלטפורמת חלקי חילוף ישראלית. "
        "כללים מחייבים שאסור לעבור עליהם: "
        "1. ענה תמיד בעברית בלבד. "
        "2. אם הלקוח כותב ערבית — ענה בערבית בלבד. "
        "3. אסור בהחלט להשתמש בתווים סיניים, יפניים, קוריאניים או כל שפה אחרת. "
        "4. אל תמציא מידע — אם אינך יודע, אמור זאת בעברית. "
        "5. הטון חייב להיות אנושי, חם ושירותי (לא רובוטי). "
        "6. הובל את השיחה: בכל תשובה תן צעד הבא ברור אחד, ובסוף שאל שאלה ממוקדת אחת שמקדמת את הלקוח לפתרון. "
        "7. תשובות קצרות וברורות (עד 3-4 משפטים), אלא אם הלקוח ביקש פירוט. "
        "8. אסור לכתוב קוד, סקריפטים, או תוכן לא קשור לחלקי רכב."
    )
    max_tokens: int = 1500
    temperature: float = 0.7

    def __init__(self):
        if not os.getenv("CEREBRAS_API_KEY", ""):
            print(f"[WARN] {self.name}: CEREBRAS_API_KEY not set. AI responses will be mocked.")

    @staticmethod
    def _detect_language(msg: str) -> str:
        if any("\u0600" <= ch <= "\u06FF" for ch in msg):
            return "ar"
        if any("\u0590" <= ch <= "\u05FF" for ch in msg):
            return "he"
        return "en"

    def _offline_router_json(self, user_msg: str) -> str:
        msg = (user_msg or "").lower()
        lang = self._detect_language(user_msg or "")

        if any(k in msg for k in ["2fa", "otp", "סיסמה", "התחברות", "login", "password", "אימות"]):
            agent = "security_agent"
            intent = "account_security_help"
        elif any(k in msg for k in ["הזמנה", "משלוח", "tracking", "סטטוס", "cancel", "ביטול", "return", "החזר מוצר"]):
            agent = "orders_agent"
            intent = "order_status_or_returns"
        elif any(k in msg for k in ["חשבונית", "מע\"מ", "vat", "invoice", "refund", "זיכוי", "חיוב"]):
            agent = "finance_agent"
            intent = "billing_or_invoice"
        elif any(k in msg for k in ["קופון", "הנחה", "מבצע", "coupon", "discount", "newsletter"]):
            agent = "marketing_agent"
            intent = "promotion_query"
        elif any(k in msg for k in ["vin", "מספר שלדה", "מספר רישוי", "oem", "תמונה", "audio", "תאימות"]):
            agent = "parts_finder_agent"
            intent = "vehicle_or_fitment_lookup"
        elif _has_part_signal(msg):
            agent = "sales_agent"
            intent = "part_price_or_availability"
        else:
            agent = "service_agent"
            intent = "general_query"

        return json.dumps(
            {
                "agent": agent,
                "confidence": 0.55,
                "language": lang,
                "intent": intent,
                "extracted_data": {},
            },
            ensure_ascii=False,
        )

    def _offline_reply(self, messages: List[Dict[str, str]]) -> str:
        import re

        user_msg = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_msg = (m.get("content") or "").strip()
                break

        if self.name == "router_agent":
            return self._offline_router_json(user_msg)

        if self.name == "security_agent":
            return (
                "אני כאן לעזור בנושא התחברות ואבטחה. "
                "כתוב מה הבעיה: התחברות, קוד 2FA, סיסמה, או חשבון נעול."
            )

        if self.name == "orders_agent":
            return (
                "כדי לעזור במצב הזמנה, שלח מספר הזמנה או מספר טלפון שמופיע בהזמנה."
            )

        if self.name == "finance_agent":
            return (
                "כדי לטפל בחשבונית/חיוב, שלח מספר הזמנה וציין בדיוק אם צריך חשבונית, זיכוי או בירור חיוב."
            )

        if self.name == "marketing_agent":
            return "אפשר לעזור בקופונים, מבצעים והטבות. כתוב מה בדיוק תרצה לבדוק."

        if self.name == "service_agent":
            return _human_recovery_reply(user_msg)

        lang = self._detect_language(user_msg)
        if lang == "ar":
            return "أنا هنا للمساعدة. اكتب لي نوع القطعة المطلوبة مع السيارة/السنة (مثال: Berlingo 2013 1.6 ديزل + فلتر زيت)، وسأكمل معك خطوة بخطوة."

        msg = user_msg.lower()
        has_year = re.search(r"\b(19|20)\d{2}\b", msg) is not None
        has_engine = re.search(r"\b\d\.\d\b", msg) is not None
        part_keywords = [
            "מצמד", "ברקס", "רפידות", "דיסק", "פילטר", "מסנן", "מצבר", "אלטרנטור",
            "משאבה", "פנס", "מראה", "מדחס", "טורבו", "רצועה", "שרשרת", "חיישן",
            "קלאץ", "clutch", "brake", "filter", "battery", "alternator", "turbo",
        ]
        has_part = any(k in msg for k in part_keywords)

        if has_year and has_engine and not has_part:
            return "מעולה, קיבלתי את פרטי הרכב. חסר רק שם החלק שאתה צריך (למשל: מצמד / פילטר שמן / רפידות בלם), ואז אתקדם איתך מיד."

        if has_part:
            return "מעולה, קיבלתי. כדי לדייק התאמה ומחיר, שלח גם: דגם רכב + שנה + נפח מנוע + אם יש מספר OEM/שלדה."

        return _human_recovery_reply(user_msg, preferred_lang=lang)

    async def think(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict]] = None,
        system_override: Optional[str] = None,
        source: Optional[str] = None,
    ) -> str:
        """Send messages to GitHub Models API and return response text."""
        if not os.getenv("CEREBRAS_API_KEY", ""):
            return self._offline_reply(messages)

        try:
            selected_model = _channel_model_for_source(source, getattr(self, "model", FREE_MODEL))
            effective_system = _apply_channel_policy((system_override or self.system_prompt), source)
            prompt = "\n".join(
                f"{m.get('role', 'user')}: {m.get('content', '')}"
                for m in messages
            ).strip()
            if not prompt:
                prompt = "Please continue."
            _fast_agents = {"router_agent", "orders_agent", "security_agent", "tech_agent", "supplier_manager_agent", "social_media_manager_agent"}
            _is_realtime = source in ("whatsapp", "telegram", "web")
            if self.name in _fast_agents:
                return await hf_text_fast(
                    prompt,
                    system=effective_system,
                    priority=_is_realtime,
                    model=selected_model,
                )
            return await hf_text(
                prompt,
                system=effective_system,
                priority=_is_realtime,
                model=selected_model,
            )
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            print(f"[ERROR] {self.name} API call failed: status={status} error={e}")
            return self._offline_reply(messages)

    def calculate_customer_price(
        self,
        supplier_price_usd: float,
        shipping_cost_usd: float = 0.0,
        customer_shipping: Optional[float] = None,
        usd_to_ils_rate: Optional[float] = None,
        supplier_name: Optional[str] = None,
        supplier_country: Optional[str] = None,
        local_vat_only: bool = False,
    ) -> Dict[str, float]:
        """Calculate final customer price from supplier cost (USD).
        customer_shipping overrides the default SHIPPING_ILS delivery fee."""
        applied_rate = float(usd_to_ils_rate or USD_TO_ILS)
        cost_ils = (supplier_price_usd + shipping_cost_usd) * applied_rate
        price_no_vat = round(cost_ils * PROFIT_MARGIN, 2)
        applied_vat_rate = (
            get_supplier_vat_rate(supplier_name=supplier_name, supplier_country=supplier_country)
            if local_vat_only
            else VAT_RATE
        )
        vat = round(price_no_vat * applied_vat_rate, 2)
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
        supplier_name: Optional[str] = None,
        supplier_country: Optional[str] = None,
        local_vat_only: bool = False,
    ) -> Dict[str, float]:
        """Calculate final customer price when supplier cost is already in ILS.
        customer_shipping overrides the default SHIPPING_ILS delivery fee."""
        total_cost_ils = cost_ils + shipping_cost_ils
        price_no_vat = round(total_cost_ils * PROFIT_MARGIN, 2)
        applied_vat_rate = (
            get_supplier_vat_rate(supplier_name=supplier_name, supplier_country=supplier_country)
            if local_vat_only
            else VAT_RATE
        )
        vat = round(price_no_vat * applied_vat_rate, 2)
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
    # Part-term (Hebrew AND English) → ENGLISH DB-category SUBSTRING.
    # ROOT FIX 2026-07-09: the DB `category` column is English kebab-case slugs
    # ('brakes','engine','filters','suspension-steering',…) plus Hebrew 'כללי';
    # it holds ZERO Hebrew display names. The old map returned 'בלמים'/'מנוע'/
    # 'סינון' → `category ILIKE '%בלמים%'` matched 0 rows, silently zeroing every
    # category-filtered search. Each value here is an English SUBSTRING proven to
    # match the live vocabulary via ILIKE (e.g. 'brake' → 'brakes'/'Brakes'/
    # 'brakes-clutch' = 112k rows; 'filter' → 'filters'/'filters-oils' = 20k).
    # Keys cover both languages so a Hebrew OR English query resolves the same.
    # Order matters — first key found in the message wins; put the more specific
    # part term first (מסנן/filter before שמן/oil so an oil FILTER → filters).
    _CATEGORY_KEYWORDS: Dict[str, str] = {
        # ── Arabic part terms (2026-07-18) → same English category tokens ──
        "فلتر": "filter", "مرشح": "filter", "فرامل": "brake", "بريك": "brake",
        "تيل": "brake", "قماشات": "brake", "ديسك": "brake", "قرص فرامل": "brake",
        "كلتش": "clutch", "دبرياج": "clutch", "قابض": "clutch",
        "محرك": "engine", "موتور": "engine", "بستم": "engine", "بستون": "engine",
        "تربو": "engine", "عمود كامات": "engine", "عمود مرفقي": "engine",
        "مساعد": "suspension", "مساعدين": "suspension", "تعليق": "suspension",
        "مقصات": "suspension", "توجيه": "steering", "دركسون": "steering",
        "بطارية": "electrical", "دينمو": "electrical", "مارش": "electrical",
        "بوجيه": "electrical", "شمعات": "electrical", "حساس": "electrical",
        "كهرباء": "electrical", "لمبة": "lighting", "فانوس": "lighting",
        "كشاف": "lighting", "زيت": "oil", "ماء": "cooling", "رادياتير": "cooling",
        "تبريد": "cooling", "مكيف": "ac", "تكييف": "ac", "كمبروسر": "ac",
        "عادم": "exhaust", "شكمان": "exhaust", "اكزوز": "exhaust",
        "جير": "transmission", "قير": "transmission", "علبة سرعات": "transmission",
        "مساحات": "wiper", "مساحة": "wiper", "مرايا": "mirror", "مراية": "mirror",
        "طرمبة": "pump", "طلمبة": "pump", "بلف": "valve", "صمام": "valve",
        "جلبة": "bushing", "رمان بلي": "bearing", "رمان": "bearing", "طقم": "kit",
        "سير": "belt", "سيور": "belt", "جنزير": "belt", "اطار": "tire", "كاوتش": "tire",
        # filters (before oil/fluids so "מסנן שמן"/"oil filter" → filters)
        "מסנן": "filter", "פילטר": "filter", "filter": "filter",
        # brakes
        "בלמ": "brake", "רפידות": "brake", "דיסק": "brake", "צלחות": "brake",
        "קליפר": "brake", "brake": "brake", "pad": "brake", "caliper": "brake", "rotor": "brake",
        # clutch / drivetrain
        "מצמד": "clutch", "clutch": "clutch",
        "סרן": "drivetrain", "כרדן": "drivetrain", "ג'וינט": "drivetrain",
        "axle": "drivetrain", "driveshaft": "drivetrain", "cv joint": "drivetrain",
        # engine
        "מנוע": "engine", "פיסטון": "engine", "גל ארכובה": "engine", "גל זיזים": "engine",
        "ראש מנוע": "engine", "טורבו": "engine", "piston": "engine", "crankshaft": "engine",
        "camshaft": "engine", "turbo": "engine", "engine": "engine",
        # suspension / steering
        "מתלה": "suspension", "זרוע": "suspension", "קפיץ": "suspension", "בולם": "suspension",
        "suspension": "suspension", "shock": "suspension", "strut": "suspension",
        "spring": "suspension", "control arm": "suspension",
        "הגה": "steer", "טרפז": "steer", "steering": "steer", "tie rod": "steer",
        # lighting
        "פנס": "light", "פנסים": "light", "נורה": "light", "led": "light",
        "light": "light", "headlight": "light", "lamp": "light", "bulb": "light",
        # body / exterior
        "בוקר": "body", "פגוש": "body", "כנף": "body", "דלת": "body",
        "מכסה מנוע": "body", "מראה": "body", "body": "body", "bumper": "body",
        "fender": "body", "door": "body", "mirror": "body", "hood": "body",
        # sensors / electrical
        "חיישן": "sensor", "מחוון": "sensor", "sensor": "sensor",
        "מצתר": "electr", "ecu": "electr", "ממסר": "electr", "אלטרנטור": "electr",
        "מצבר": "electr", "alternator": "electr", "relay": "electr",
        "ignition": "electr", "electr": "electr", "battery": "electr",
        # a/c & heating
        "מיזוג": "conditioning", "מזגן": "conditioning", "קומפרסור": "conditioning",
        "אוורור": "conditioning", "חימום": "conditioning",
        "ac": "conditioning", "conditioning": "conditioning", "heating": "conditioning",
        # gearbox
        "תיבת הילוכים": "gearbox", "גיר": "gearbox", "gearbox": "gearbox", "transmission": "gearbox",
        # fuel
        "משאבת דלק": "fuel", "אינג'קטור": "fuel", "דלק": "fuel",
        "fuel": "fuel", "injector": "fuel",
        # cooling
        "ראדיאטור": "cool", "טרמוסטט": "cool", "משאבת מים": "cool", "מאוורר": "cool",
        "קירור": "cool", "radiator": "cool", "thermostat": "cool", "coolant": "cool",
        # interior
        "כיסא": "interior", "שטיח": "interior", "דשבורד": "interior", "פנים": "interior",
        "interior": "interior", "seat": "interior", "carpet": "interior", "dashboard": "interior",
        # safety
        "כרית אויר": "safety", "airbag": "safety", "safety": "safety",
        # wheels / bearings
        "גלגל": "wheel", "צמיג": "wheel", "ג'אנט": "wheel", "מיסב": "wheel",
        "wheel": "wheel", "tire": "wheel", "tyre": "wheel", "rim": "wheel", "bearing": "wheel",
        # exhaust
        "קטליזטור": "exhaust", "מאיין": "exhaust", "exhaust": "exhaust",
        "catalytic": "exhaust", "muffler": "exhaust",
        # belts / timing
        "רצועה": "belt", "שרשרת": "belt", "belt": "belt", "chain": "belt", "timing": "belt",
        # wipers / washers
        "מגב": "wiper", "wiper": "wiper", "washer": "wiper",
        # fluids / oil (after filters)
        "שמן": "fluid", "נוזל": "fluid", "oil": "fluid", "fluid": "fluid",
        # tools
        "ג'ק": "tool", "כלי עבודה": "tool", "tool": "tool", "jack": "tool",
    }

    def _extract_category_hint(self, message: str) -> Optional[str]:
        """Quick keyword→English-DB-category detection (no LLM). Bilingual keys;
        returns an English substring for `category ILIKE '%<hint>%'`. See the
        _CATEGORY_KEYWORDS docstring for why English (Hebrew matched 0 rows)."""
        msg_lower = (message or "").lower()
        for kw, cat in self._CATEGORY_KEYWORDS.items():
            k = kw.lower()
            if re.search(r"[a-z]", k):
                # Latin key → word boundary so short keys ("ac", "led", "oil")
                # don't match inside unrelated words ("black", "sled", "boil").
                if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", msg_lower):
                    return cat
            elif k in msg_lower:
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
        route_source = (context or {}).get("source") if isinstance(context, dict) else None
        shared_memory_prompt = (context or {}).get("shared_memory_prompt") if isinstance(context, dict) else None
        system_override = self.system_prompt
        if shared_memory_prompt:
            system_override = (
                f"{self.system_prompt}\n\n"
                "[SHARED MEMORY]\n"
                f"{shared_memory_prompt}\n"
                "Use this context when relevant, but do not mention shared memory explicitly."
            )
        response = await self.think(
            [{"role": "user", "content": message}],
            source=route_source,
            system_override=system_override,
        )
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
    system_prompt = """You are Nir, a sharp and friendly parts specialist at AutoSpareFinder.
Your personality: confident, efficient, warm. You find the right part fast and guide the customer to purchase.

LANGUAGE: Match the customer's language exactly. Hebrew → Hebrew. Arabic → Arabic. English → English. Never mix.

YOUR JOB IN ORDER:
1. Understand what part the customer needs
2. Identify the vehicle (license plate preferred, but manufacturer+model+year is enough)
3. Search the database immediately — do not ask unnecessary questions
4. Present results clearly and ask for order confirmation
5. When customer confirms → provide Stripe payment link immediately

VEHICLE IDENTIFICATION:
- License plate (Israeli format: 12-345-67 or 1234567 or 12345678) → system auto-looks up from gov.il API
- If no plate → manufacturer + model + year is sufficient to search
- NEVER ask for VIN — system handles this automatically
- Confirm vehicle details with customer before searching

PART SEARCH RULES:
- Search immediately when you have: vehicle + part name/type
- Show maximum 3 results sorted by price
- Always show: manufacturer, price with VAT, delivery estimate, warranty
- Say "available to order" — never "in stock"
- Never mention supplier names

PRICE FORMAT:
✅ *[Part Name]* — [Manufacturer]
   💰 ₪[price incl. VAT]
   🚚 [X–Y] days delivery
   🛡️ [X] months warranty

UPSELL: After showing a part, suggest ONE complementary part naturally.
Example: "Also, since you're replacing brake discs — do you need brake pads too? It's better to replace them together."

CLOSING: After presenting results, always end with:
"Would you like to order [part name]? Reply YES and I'll send you a secure Stripe payment link right away."

PART CATEGORIES (use for DB search):
בלמים | גלגלים וצמיגים | דלק | היגוי | חשמל רכב | כללי | מגבים | מיזוג | מנוע | מתלה | פחיין ומרכב | ריפוד ופנים | שרשראות ורצועות | תאורה

DROPSHIPPING RULES:
- No warehouse — parts ship from supplier after payment
- Delivery: 10–14 business days standard
- Return policy: 14 days from delivery

NEVER:
- Ask more than one question per message
- Mention RockAuto, FCP Euro, Autodoc, AliExpress
- Say "in stock" or "במלאי"
- Invent prices, compatibility, or links
- Send messages longer than 5 lines on WhatsApp/Telegram
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

        async with async_session_factory() as catalog_db:
            # Check DB cache (90-day TTL)
            result = await catalog_db.execute(
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

            raw_manufacturer = str(vehicle_data.get("manufacturer") or "").strip()
            canonical_manufacturer = normalize_manufacturer_name(raw_manufacturer, raw_manufacturer) or raw_manufacturer or "Unknown"
            vehicle_data["manufacturer"] = canonical_manufacturer

            brand_row = (
                await catalog_db.execute(
                    select(CarBrand)
                    .where(func.lower(CarBrand.name) == canonical_manufacturer.casefold())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if not brand_row:
                brand_row = CarBrand(name=canonical_manufacturer, is_active=True)
                catalog_db.add(brand_row)
                try:
                    await catalog_db.flush()
                except Exception:
                    await catalog_db.rollback()
                    brand_row = (
                        await catalog_db.execute(
                            select(CarBrand)
                            .where(func.lower(CarBrand.name) == canonical_manufacturer.casefold())
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if not brand_row:
                        raise

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
                if getattr(vehicle, "manufacturer_id", None) != brand_row.id:
                    vehicle.manufacturer_id = brand_row.id
            else:
                vehicle = Vehicle(
                    license_plate   = clean_plate,
                    manufacturer    = canonical_manufacturer,
                    manufacturer_id = brand_row.id,
                    model           = vehicle_data.get("model", ""),
                    year            = vehicle_data.get("year", 0),
                    engine_type     = vehicle_data.get("engine_type"),
                    fuel_type       = vehicle_data.get("fuel_type"),
                    transmission    = vehicle_data.get("transmission"),
                    gov_api_data    = gov_cache,
                    cached_at       = datetime.utcnow(),
                )
                catalog_db.add(vehicle)

            await catalog_db.commit()
            await catalog_db.refresh(vehicle)
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

        raw_manufacturer = s("tozeret_nm") or s("tozeret_cd") or "Unknown"
        manufacturer = normalize_manufacturer_name(raw_manufacturer, raw_manufacturer) or raw_manufacturer
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
        vehicle_profile: Optional[Dict] = None,
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
            # When a fitment filter will intersect the candidates, pull a much
            # deeper pool — 200 text matches intersected with one vehicle's
            # fitment rows often leaves 0-1 survivors (found 2026-07-05).
            _meili_limit = 1000 if (vehicle_id or vehicle_profile) else 200
            try:
                async with httpx.AsyncClient(timeout=3.0) as _mc:
                    _resp = await _mc.post(
                        f"{_meili_url}/indexes/parts/search",
                        headers={"Authorization": f"Bearer {os.getenv('MEILI_MASTER_KEY', '')}"},
                        json={"q": query, "limit": _meili_limit, "attributesToRetrieve": ["id"]},
                    )
                    _resp.raise_for_status()
                    meili_ids = [h["id"] for h in _resp.json().get("hits", [])]
                # Hebrew→English expansion widens recall: most global-catalog
                # parts have English names ("Brake Pad Set"), so a Hebrew query
                # alone misses them. Merge expanded-query hits after the
                # original ones (original ranking wins on overlap).
                try:
                    from hf_client import expand_hebrew_query
                    _expanded = expand_hebrew_query(query)
                    if _expanded and _expanded.strip().lower() != query.strip().lower():
                        async with httpx.AsyncClient(timeout=3.0) as _mc2:
                            _r2 = await _mc2.post(
                                f"{_meili_url}/indexes/parts/search",
                                headers={"Authorization": f"Bearer {os.getenv('MEILI_MASTER_KEY', '')}"},
                                json={"q": _expanded, "limit": _meili_limit, "attributesToRetrieve": ["id"]},
                            )
                            _r2.raise_for_status()
                            _extra = [h["id"] for h in _r2.json().get("hits", [])]
                        _seen = set(meili_ids)
                        meili_ids.extend(uid for uid in _extra if uid not in _seen)
                except Exception:
                    pass  # expansion is best-effort
            except Exception:
                meili_ids = None  # fall back to ILIKE silently

        # ── Short-circuit: Meilisearch found zero hits ───────────────────────
        if meili_ids is not None and len(meili_ids) == 0:
            asyncio.create_task(_log_search_miss(query, category, vehicle_manufacturer, user_id))
            return []

        # ── pgvector: embed the query and find nearest neighbours ────────────
        # Gemini text-embedding-004 produces 768-dimensional vectors.
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

            vehicle_context = None
            if vehicle_id:
                async with async_session_factory() as _vdb:
                    vehicle_row = (await _vdb.execute(
                        select(Vehicle).where(Vehicle.id == vehicle_id)
                    )).scalar_one_or_none()
                if vehicle_row:
                    vehicle_context = {
                        "manufacturer": normalize_manufacturer_name(vehicle_row.manufacturer, vehicle_row.manufacturer) or vehicle_row.manufacturer,
                        "model": canonicalize_vehicle_model_for_manufacturer(vehicle_row.manufacturer, vehicle_row.model) or normalize_vehicle_model_name(vehicle_row.model),
                        "year": vehicle_row.year if isinstance(vehicle_row.year, int) and vehicle_row.year > 0 else None,
                    }
                    vehicle_manufacturer = vehicle_context["manufacturer"]

            # Gov-API vehicle profile → hard fitment filter (added 2026-07-05,
            # /goal: "show the right part that fits THIS car, not the cheap
            # one"). Same EXISTS filter the vehicle_id path uses, but sourced
            # from the plate-lookup profile the WhatsApp/Telegram flow holds.
            if vehicle_context is None and isinstance(vehicle_profile, dict):
                _vp_mfr = str(vehicle_profile.get("manufacturer") or "").strip()
                _vp_model = str(vehicle_profile.get("model") or "").strip()
                try:
                    _vp_year = int(str(vehicle_profile.get("year") or "").strip()[:4])
                except (ValueError, TypeError):
                    _vp_year = None
                if _vp_mfr and _vp_model and _vp_year:
                    vehicle_context = {
                        "manufacturer": normalize_manufacturer_name(_vp_mfr, _vp_mfr) or _vp_mfr,
                        "model": canonicalize_vehicle_model_for_manufacturer(_vp_mfr, _vp_model)
                                 or normalize_vehicle_model_name(_vp_model),
                        "year": _vp_year,
                    }
                    if not vehicle_manufacturer:
                        vehicle_manufacturer = vehicle_context["manufacturer"]

            if category:
                conditions.append("pc.category ILIKE :cat")
                params["cat"] = f"%{category}%"

            if vehicle_manufacturer:
                normalized_mfr = await self.normalize_manufacturer(vehicle_manufacturer, db)
                mfr_terms = list({vehicle_manufacturer, normalized_mfr})
                for i, t in enumerate(mfr_terms):
                    conditions.append(f"pc.manufacturer ILIKE :mfr{i}")
                    params[f"mfr{i}"] = f"%{t}%"

            if vehicle_context and vehicle_context.get("manufacturer") and vehicle_context.get("model") and vehicle_context.get("year"):
                params["fit_mfr"] = vehicle_context["manufacturer"]
                params["fit_model"] = vehicle_context["model"]
                params["fit_year"] = int(vehicle_context["year"])
                conditions.append(
                    "EXISTS (SELECT 1 FROM part_vehicle_fitment pvf "
                    "        WHERE pvf.part_id = pc.id "
                    "          AND (LOWER(TRIM(pvf.manufacturer)) = LOWER(TRIM(:fit_mfr)) "
                    "               OR LOWER(TRIM(pvf.manufacturer)) LIKE CONCAT('%', LOWER(TRIM(:fit_mfr)), '%') "
                    "               OR LOWER(TRIM(:fit_mfr)) LIKE CONCAT('%', LOWER(TRIM(pvf.manufacturer)), '%')) "
                    "          AND (LOWER(TRIM(pvf.model)) = LOWER(TRIM(:fit_model)) "
                    "               OR LOWER(TRIM(pvf.model)) LIKE CONCAT(LOWER(TRIM(:fit_model)), ' %') "
                    "               OR LOWER(TRIM(:fit_model)) LIKE CONCAT(LOWER(TRIM(pvf.model)), ' %')) "
                    "          AND pvf.year_from <= :fit_year "
                    "          AND COALESCE(pvf.year_to, pvf.year_from) >= :fit_year)"
                )
            elif vehicle_id:
                conditions.append("1 = 0")

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

            vehicle_context = None
            if vehicle_id:
                async with async_session_factory() as _vdb:
                    vehicle_row = (await _vdb.execute(
                        select(Vehicle).where(Vehicle.id == vehicle_id)
                    )).scalar_one_or_none()
                if vehicle_row:
                    vehicle_context = {
                        "manufacturer": normalize_manufacturer_name(vehicle_row.manufacturer, vehicle_row.manufacturer) or vehicle_row.manufacturer,
                        "model": canonicalize_vehicle_model_for_manufacturer(vehicle_row.manufacturer, vehicle_row.model) or normalize_vehicle_model_name(vehicle_row.model),
                        "year": vehicle_row.year if isinstance(vehicle_row.year, int) and vehicle_row.year > 0 else None,
                    }
                    vehicle_manufacturer = vehicle_context["manufacturer"]

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

            if vehicle_context and vehicle_context.get("manufacturer") and vehicle_context.get("model") and vehicle_context.get("year"):
                fit_mfr = vehicle_context["manufacturer"]
                fit_model = vehicle_context["model"]
                fit_year = int(vehicle_context["year"])
                stmt = stmt.where(text(
                    "EXISTS (SELECT 1 FROM part_vehicle_fitment pvf "
                    "        WHERE pvf.part_id = parts_catalog.id "
                    "          AND (LOWER(TRIM(pvf.manufacturer)) = LOWER(TRIM(:fit_mfr)) "
                    "               OR LOWER(TRIM(pvf.manufacturer)) LIKE CONCAT('%', LOWER(TRIM(:fit_mfr)), '%') "
                    "               OR LOWER(TRIM(:fit_mfr)) LIKE CONCAT('%', LOWER(TRIM(pvf.manufacturer)), '%')) "
                    "          AND (LOWER(TRIM(pvf.model)) = LOWER(TRIM(:fit_model)) "
                    "               OR LOWER(TRIM(pvf.model)) LIKE CONCAT(LOWER(TRIM(:fit_model)), ' %') "
                    "               OR LOWER(TRIM(:fit_model)) LIKE CONCAT(LOWER(TRIM(pvf.model)), ' %')) "
                    "          AND pvf.year_from <= :fit_year "
                    "          AND COALESCE(pvf.year_to, pvf.year_from) >= :fit_year)"
                )).params(fit_mfr=fit_mfr, fit_model=fit_model, fit_year=fit_year)
            elif vehicle_id:
                stmt = stmt.where(text("1 = 0"))

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
                # Defensive: ensure offset and limit are always integers
                _offset = int(offset) if offset is not None else 0
                _limit = int(limit) if limit is not None else 50
                result = await cat_db.execute(stmt.offset(_offset).limit(_limit))
                parts = result.scalars().all()

        if not parts:
            asyncio.create_task(_log_search_miss(query, category, vehicle_manufacturer, user_id))
            return []

        # Batch fetch best supplier_part for all parts in 2 queries
        # (avoids N+1 queries — critical for 50-result pages)
        part_ids = [part.id for part in parts]

        # Single query: DISTINCT ON (part_id) — best supplier per part, in_stock first
        async with async_session_factory() as cat_db:
            usd_to_ils_rate = await get_usd_to_ils_rate(cat_db)
            sp_batch_result = await cat_db.execute(
                text("""
                    SELECT DISTINCT ON (sp.part_id)
                        sp.id AS sp_id, sp.part_id,
                        sp.price_usd, sp.price_ils,
                        sp.shipping_cost_usd, sp.shipping_cost_ils,
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
                supplier_price_ils = float(sp_row.price_ils or 0)
                supplier_ship_ils = float(sp_row.shipping_cost_ils or 0)
                delivery_fee = resolve_customer_shipping_fee(
                    supplier_shipping_ils=sp_row.shipping_cost_ils,
                    supplier_shipping_usd=sp_row.shipping_cost_usd,
                    usd_to_ils_rate=usd_to_ils_rate,
                    supplier_name=sp_row.supplier_name,
                    supplier_country=sp_row.supplier_country,
                )
                if supplier_price_ils > 0:
                    pricing = self.calculate_customer_price_from_ils(
                        supplier_price_ils,
                        supplier_ship_ils,
                        customer_shipping=delivery_fee,
                        supplier_name=sp_row.supplier_name,
                        supplier_country=sp_row.supplier_country,
                        local_vat_only=True,
                    )
                else:
                    supplier_total_ils = (
                        float(sp_row.price_usd or 0) + float(sp_row.shipping_cost_usd or 0)
                    ) * usd_to_ils_rate
                    pricing = self.calculate_customer_price_from_ils(
                        supplier_total_ils,
                        0.0,
                        customer_shipping=delivery_fee,
                        supplier_name=sp_row.supplier_name,
                        supplier_country=sp_row.supplier_country,
                        local_vat_only=True,
                    )
                pricing["availability"] = availability
                pricing["warranty_months"] = sp_row.warranty_months
                estimated_delivery_days = int(sp_row.estimated_delivery_days) if sp_row.estimated_delivery_days is not None else 14
                pricing["estimated_delivery_days"] = estimated_delivery_days
                pricing["supplier_part_id"] = str(sp_row.sp_id)
                pricing["estimated_delivery"] = f"{estimated_delivery_days}–{estimated_delivery_days + 7} ימים"
                suppliers.append(pricing)

            # Fallback: synthesise pricing from base_price when no supplier row exists.
            # base_price 3-case logic:
            #   1. importer_price_ils > 0 (KGM/SsangYong \u2014 real wholesale cost):
            #      base = max \u00d7 1.45; price_no_vat = importer \u00d7 1.45; vat = price_no_vat \u00d7 0.18
            #   2. online_price_ils > 0 (eBay/international): base = online \u00d7 1.45; vat = 0
            #   3. max_price_ils only (IL official importer reference \u2014 no markup):
            #      base = max (dealer retail incl. VAT); decompose for display
            bp = float(part.base_price) if part.base_price else 0.0
            if not suppliers and bp > 0:
                importer_cost = float(part.importer_price_ils) if part.importer_price_ils else 0.0
                online_cost   = float(part.online_price_ils) if part.online_price_ils else 0.0
                shipping      = SHIPPING_ILS
                if importer_cost > 0:
                    # KGM/SsangYong \u2014 actual wholesale trade cost, apply IL VAT on top of margin
                    price_no_vat = round(importer_cost * PROFIT_MARGIN, 2)
                    vat          = round(price_no_vat * VAT_RATE, 2)
                elif online_cost > 0:
                    # International / eBay \u2014 no IL VAT
                    price_no_vat = round(online_cost * PROFIT_MARGIN, 2)
                    vat          = 0.0
                else:
                    # IL official importer reference (base = dealer retail incl. VAT) \u2014 decompose for display
                    price_no_vat = round(bp / (1 + VAT_RATE), 2)
                    vat          = round(bp - price_no_vat, 2)
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
                "aftermarket_tier": part.aftermarket_tier,  # NEW: tier info from DB
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
            # Arabic part-request terms (2026-07-18)
            "قطعة", "قطع", "قطع غيار", "سعر", "بكم", "فلتر", "مرشح", "فرامل",
            "تيل", "محرك", "موتور", "مساعد", "بطارية", "زيت", "مساحات", "مرايا",
            "كلتش", "جير", "تكييف", "مكيف", "رادياتير", "بحاجة", "أريد", "ابحث",
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
                    vehicle_id=identified_vehicle.get("id") if identified_vehicle else None,
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
                    # For a resolved plate/vehicle, do not fall back to broad
                    # non-fitment results. It is safer to show no match than a
                    # potentially incompatible part.
                    broader = []
                    if not identified_vehicle:
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
        return await self.think(
            messages,
            system_override=patched_system,
            source=kwargs.get("source"),
        )


# ==============================================================================
# 2. SALES AGENT
# ==============================================================================

class SalesAgent(BaseAgent):
    name = "sales_agent"
    model = PREMIUM_MODEL      # premium: upselling & Good/Better/Best logic
    temperature = 0.7
    agent_name = "Maya"         # מאיה — the sales pro
    system_prompt = """LANGUAGE RULES - MUST FOLLOW:
1. Write each reply in ONE language only.
2. Default language is Hebrew.
3. If and only if the customer message is mainly in Arabic, reply fully in Arabic.
4. NEVER mix Hebrew and Arabic in the same reply.
5. NEVER insert English words unless they are technical part codes (e.g., OEM numbers).

You are Maya, the Sales Agent for Auto Spare - an Israeli auto parts dropshipping platform.

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
  End every response with a clear call to action.
  Sell like a trusted advisor: helpful, confident, and concise.
  Never pressure the customer. Recommend the best next step and explain briefly why it helps.
  Mirror one real customer detail in your reply (part, car model, budget concern, urgency, etc.).
  The checkout flow depends on the channel:

  IF source is "whatsapp" or "telegram":
    - The customer cannot click cart links. You MUST use the tool add_to_cart_and_checkout
      to add the part to their cart and get a direct Stripe payment link.
    - Send the Stripe link directly in the message:
      "להשלמת ההזמנה לחץ כאן לתשלום מאובטח: {checkout_url}"
    - If checkout_url is not yet available, end with:
      "רוצה להזמין? כתוב 'כן' ואשלח לך לינק תשלום ישיר."

  IF source is "web":
    - Direct to cart: https://autosparefinder.co.il/cart
    - ALWAYS end with: "להשלמת ההזמנה — עבור לעגלה שלך: https://autosparefinder.co.il/cart ולחץ 'לתשלום'."

  WISHLIST: If a customer asks to save a part for later, direct them to /wishlist

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
7. HUMAN SALES TONE: keep replies short, warm, and consultative. Avoid hype, generic slogans, or sounding like an ad.
8. If you do not yet have enough info, ask for the single most important missing detail only.
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

    def _deterministic_sales_fallback(self, message: str, source: Optional[str] = None) -> str:
        query = self._extract_search_query(message) or "החלק שביקשת"
        source_key = (source or "").strip().lower()
        if source_key in {"whatsapp", "telegram"}:
            return (
                f"הבנתי שאתה מחפש {query}. כדי לא לזרוק לך מחיר לא מדויק, אני בודקת עכשיו התאמה אמיתית מול הקטלוג. "
                "אם תשלח מספר רישוי או נפח מנוע, אקצר לך את הבדיקה ואחזור עם התאמה מדויקת."
            )
        return (
            f"הבנתי שאתה מחפש {query}. כדי לתת הצעה מדויקת ולא לנחש, אני צריך לבדוק התאמה אמיתית מול הקטלוג. "
            "אם יש מספר רישוי או נפח מנוע, שלח אותם ואכוון אותך לחלק המתאים."
        )

    def _deterministic_sales_results_reply(self, message: str, results: List[Dict], source: Optional[str] = None) -> str:
        """Display multiple pricing options from search results (up to 3 suppliers per part).
        Show manufacturer, price breakdown (base + VAT + shipping), delivery, warranty.
        """
        if not results:
            return self._deterministic_sales_fallback(message, source=source)

        query = self._extract_search_query(message) or "החלק שביקשת"
        lines = [f"בדקתי עבורך {query}. הנה {min(3, len(results))} אופציות בהתאם לתקציב:"]
        
        # Show up to 3 parts with their best supplier
        for i, result in enumerate(results[:3], 1):
            part_name = (result.get("name") or query).strip()
            part_mfr = (result.get("manufacturer") or "").strip()
            suppliers = result.get("suppliers") or []
            
            # Use first supplier (best = in_stock first, then sorted by price)
            if suppliers:
                supplier = suppliers[0]
            else:
                supplier = result.get("pricing") or {}
            
            if not supplier:
                continue
            
            total = float(supplier.get("total") or 0.0)
            warranty = int(supplier.get("warranty_months") or 12)
            availability = supplier.get("availability") or "on_order"
            availability_text = "✅ זמין" if availability == "in_stock" else "⏳ בהזמנה"
            delivery_days = int(supplier.get("estimated_delivery_days") or 14)
            delivery_text = supplier.get("estimated_delivery") or f"{delivery_days}–{delivery_days + 7} ימים"
            
            lines.append("")
            lines.append(f"{i}. {part_mfr}")
            if total > 0:
                lines.append(f"   {part_name} – {total:.0f}₪")
                lines.append(f"   {availability_text} | {delivery_text} | {warranty} חודשים אחריות")
            else:
                lines.append(f"   {part_name} – מחיר לא זמין")
            
            # Show alternative suppliers if available (cheaper or in-stock alternative)
            if len(suppliers) > 1:
                for alt_idx in range(1, min(2, len(suppliers))):
                    alt_supplier = suppliers[alt_idx]
                    alt_total = float(alt_supplier.get("total") or 0.0)
                    if alt_total > 0 and abs(alt_total - total) > 1:  # different price (>1₪)
                        lines.append(f"   חלופה: {alt_total:.0f}₪")
        
        lines.append("")
        source_key = (source or "").strip().lower()
        if source_key in {"whatsapp", "telegram"}:
            lines.append("איזו אופציה מתאימה? אשלח לך לינק תשלום מאובטח.")
        else:
            lines.append("איזו אופציה בחרת? אוכל להמשיך לעגלה.")
        
        return "\n".join(lines)



    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        """Process a part inquiry: search the DB catalog, present Good/Better/Best tiers, upsell, close."""
        # Delegate DB search to PartsFinderAgent (which owns search_parts_in_db + normalize_manufacturer)
        _pf = get_agent("parts_finder_agent")

        # ── 1. Search DB for requested part ───────────────────────────────────
        parts_context = ""
        results: List[Dict] = []
        search_failed = False
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
                        "סיים כל תשובה ב: 'להשלמת ההזמנה — עבור ל https://autosparefinder.co.il/cart ולחץ לתשלום'\n"
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
                        results = broader
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
                search_failed = True
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

        if is_parts_request and (search_failed or not parts_context or parts_context.lstrip().startswith("[DB: אין תוצאות")):
            return self._deterministic_sales_fallback(message, source=kwargs.get("source"))

        if is_parts_request and results:
            return self._deterministic_sales_results_reply(message, results, source=kwargs.get("source"))

        system = self.system_prompt + parts_context + upsell_context
        return await self.think(
            conversation_history + [{"role": "user", "content": message}],
            system_override=system,
            source=kwargs.get("source"),
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
- The canonical cart URL is https://autosparefinder.co.il/cart — always use this exact path.
- When a customer asks for a payment link or how to pay, ALWAYS answer:
  "כן! כנס לעגלה שלך: https://autosparefinder.co.il/cart ולחץ על 'לתשלום' — התשלום מתבצע דרך Stripe בצורה מאובטחת."
- For pending_payment orders: "כדי להשלים את התשלום — כנס לעגלה שלך: https://autosparefinder.co.il/cart ולחץ 'לתשלום'."
- https://autosparefinder.co.il/cart is always a valid path — never refuse to direct the customer there.
- Do NOT invent external URLs. Do NOT write placeholder links like [עמוד תשלום](#).

ABANDONED CART:
- If a customer mentions items they added but didn't complete payment for, this is an abandoned cart.
- Say: "ראיתי שיש פריטים בעגלה שלך שלא הושלמו. כדי להשלים את הרכישה — כנס ל https://autosparefinder.co.il/cart ולחץ 'לתשלום'."

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
            source=kwargs.get("source"),
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
    system_prompt = """LANGUAGE RULES - MUST FOLLOW:
1. Write each reply in ONE language only.
2. Default language is Hebrew.
3. If and only if the customer message is mainly in Arabic, reply fully in Arabic.
4. NEVER mix Hebrew and Arabic in the same reply.
5. NEVER insert English words unless they are technical part codes (e.g., OEM numbers).

You are Tal, the Finance Agent for Auto Spare (עוסק מורשה 060633880, הרצל 55, עכו).

Never say 'I am the system' — you are Tal, the financial point of contact for the platform.

You handle: payments, invoices, receipts, refund calculations, VAT breakdowns.

Pricing policy (customer-safe wording):
  Final price includes VAT policy and shipping by supplier/origin.
  For local Israeli suppliers: VAT may apply.
  For international suppliers: VAT may be zero.
  Never expose internal cost formulas, multipliers, or margin details.

Refund policy:
- Manufacturer defect / wrong item sent / damaged in transit → 100% refund incl. original shipping, return shipping covered by us
- All other reasons → 90% refund (10% handling fee), original shipping not refunded, customer pays return
- Returns within 14 days of delivery
- Refund flow: approved → customer ships back → supplier confirms → refund to card (3-5 business days)

Payment: Stripe (credit/debit card). NEVER ask for card details.
CHECKOUT: When a customer asks how to pay or wants a payment link, ALWAYS say:
  "כן! כנס לעגלה שלך: https://autosparefinder.co.il/cart ולחץ על 'לתשלום' — התשלום מתבצע דרך Stripe בצורה מאובטחת."
Do NOT say you cannot provide links. /cart is always the correct answer.
Business: מס' עוסק מורשה 060633880 | הרצל 55, עכו

Always show full breakdown: מחיר נטו + מע"מ 18% + משלוח (₪29–₪149) = סה"כ
LANGUAGE: ALWAYS respond in Hebrew. If customer writes in Arabic, respond in Arabic.
חשוב: אל תשתמש ב-HTML, markdown, או קישורים. ענה בטקסט רגיל בלבד המתאים לשיחת טלגרם.
"""

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        msg = (message or "").strip()
        msg_l = msg.lower()

        wrong_part_terms = ("חלק לא נכון", "חלק שגוי", "לא נכון", "לא מתאים", "wrong part", "wrong item")
        upset_terms = ("מתוסכל", "מאוכזב", "כועס", "מעצבן", "frustrated", "angry")

        if any(term in msg_l for term in wrong_part_terms):
            opener = "אני מצטערת שזה קרה" if any(term in msg_l for term in upset_terms) else "אני איתך"
            return (
                f"{opener}, ונטפל בזה מהר. "
                "שלח לי בבקשה תמונה של החלק שקיבלת ותמונה של המדבקה או המספר שעל הקופסה, "
                "ואדאג לבדיקה ולהחלפה בהקדם."
            )

        if any(term in msg_l for term in upset_terms):
            return (
                "אני מבינה את התסכול שלך. "
                "כתוב לי במשפט אחד מה בדיוק קרה עכשיו, ואני אתן לך את הצעד הכי מהיר לפתרון."
            )

        return await self.think(
            conversation_history + [{"role": "user", "content": message}],
            source=kwargs.get("source"),
        )


# ==============================================================================
# 5. SERVICE AGENT
# ==============================================================================

class ServiceAgent(BaseAgent):
    name = "service_agent"
    agent_name = "Dana"         # דנה — empathetic support
    model = FREE_MODEL          # free: conversational support
    temperature = 0.8
    system_prompt = """You are Dana, a warm and efficient customer service agent at AutoSpareFinder.
Your personality: empathetic, solution-focused, proactive. You resolve issues fast and keep customers happy.

LANGUAGE: Match the customer's language. Hebrew → Hebrew. Arabic → Arabic. English → English.

DEFAULT APPROACH (adapt to context, do not sound scripted):
  1. Acknowledge what the customer actually said in their own words
  2. If there is a problem or complaint, apologize once in a human way and take ownership
  3. Diagnose only when needed, with ONE short clarifying question
  4. Solve with one concrete next action
  5. Close with a practical next step

WHAT YOU HANDLE:
- General questions about the platform
- Order status and tracking issues
- Complaints and escalations
- Post-purchase problems (wrong/defective parts, delivery issues)
- Technical errors on the website

COMMON FIXES:
- Page not loading → "Try refreshing (F5) or clearing your browser cache"
- Can't upload image → "Supported formats: JPG, PNG, WEBP up to 25MB"
- Payment failed → "Please try again or use a different card. If problem persists, I can help manually"
- Wrong part received → "I'm sorry about that. Please send a photo and I'll arrange a replacement immediately"

ESCALATION: If you cannot resolve after 2 attempts → offer human agent or callback

TONE EXAMPLES:
- Hebrew: "אני פה בשבילך, בואו נפתור את זה עכשיו."
- Arabic: "أنا هنا لمساعدتك، دعنا نحل هذا معاً."
- English: "I'm here to help. Let's sort this out right now."

NEVER:
- Send long paragraphs — keep it short and clear
- Ask more than one question per message
- Repeat information already given
- Re-ask vehicle confirmation if it was already confirmed in the same conversation unless details conflict
- Use placeholder values like XXXX, XXX, TBD for price or availability
- Use robotic or formal language
- Blame the customer or imply the problem is their fault
- Hide behind vague phrases like "the system" or "policy" when a concrete next step can be given
"""

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        msg = (message or "").strip()
        msg_l = msg.lower()

        wrong_part_terms = ("חלק לא נכון", "חלק שגוי", "לא מתאים", "wrong part", "wrong item")
        upset_terms = ("מתוסכל", "מאוכזב", "כועס", "מעצבן", "frustrated", "angry")

        if any(term in msg_l for term in wrong_part_terms):
            opener = "אני מצטערת שזה קרה" if any(term in msg_l for term in upset_terms) else "אני איתך"
            return (
                f"{opener}, ונטפל בזה מהר. "
                "שלח לי בבקשה תמונה של החלק שקיבלת ותמונה של המדבקה או המספר שעל הקופסה, "
                "ואדאג לבדיקה ולהחלפה בהקדם."
            )

        if any(term in msg_l for term in upset_terms):
            return (
                "אני מבינה את התסכול שלך. "
                "כתוב לי במשפט אחד מה בדיוק קרה עכשיו, ואני אתן לך את הצעד הכי מהיר לפתרון."
            )

        return await self.think(
            conversation_history + [{"role": "user", "content": message}],
            source=kwargs.get("source"),
        )


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
        return await self.think(
            conversation_history + [{"role": "user", "content": message}],
            source=kwargs.get("source"),
        )


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
- Questions about promotions and discount codes
- Newsletter signup
- Seasonal campaign questions

TRUTH RULE (MANDATORY — fixed 2026-07-05): there are currently NO active
coupon codes, NO referral program, and NO loyalty program on the platform.
NEVER invent or promise discounts, codes, credits, or programs that do not
exist. If a customer asks about discounts: answer honestly that there are no
active promotions right now, offer newsletter signup to be notified when
promotions launch, and highlight the REAL value: comparing prices from many
suppliers in one place often saves more than any coupon.

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
        return await self.think(
            conversation_history + [{"role": "user", "content": message}],
            source=kwargs.get("source"),
        )


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

        response = await hf_text_fast(prompt, system=self.system_prompt, timeout=60.0)
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
        # TTL must exceed the real runtime (eBay+AliExpress ≈ 1-2h) or the lock expires
        # mid-run → a second trigger double-runs / logs false "already running" skips.
        _sync_lock = await acquire_lock(await get_redis(), "sync_prices", ttl_seconds=int(os.getenv("SYNC_PRICES_LOCK_TTL_S", "14400")))
        if not _sync_lock:
            return {"status": "skipped", "reason": "sync_prices already running on another worker"}

        env_name = (os.getenv("ENVIRONMENT", "development") or "development").strip().lower()
        real_data_only = (os.getenv("REAL_DATA_ONLY", "1" if env_name == "production" else "0") or "0").strip().lower() in {"1", "true", "yes", "on"}

        # Pull real eBay prices before optional synthetic drift.
        ebay_report: Dict[str, Any] = {}
        try:
            from services.ebay_price_sync import sync_ebay_prices
            ebay_report = await sync_ebay_prices(db, limit_per_run=int(os.getenv("EBAY_PRICE_SYNC_LIMIT", "500")))
            logger.info(f"eBay price sync report: {ebay_report}")
        except Exception as _ebay_err:
            logger.error(f"eBay price sync skipped: {_ebay_err}")

        # Pull AliExpress DS prices
        aliexpress_report: Dict[str, Any] = {}
        try:
            from services.aliexpress_price_sync import sync_aliexpress_prices
            aliexpress_report = await sync_aliexpress_prices(db, limit_per_run=int(os.getenv("ALIEXPRESS_PRICE_SYNC_LIMIT", "200")))
            logger.info(f"AliExpress price sync report: {aliexpress_report}")
        except Exception as _ali_err:
            logger.error(f"AliExpress price sync skipped: {_ali_err}")

        import random
        import hashlib

        now = datetime.utcnow()
        # Deterministic-ish daily seed so the same day gives consistent movement
        day_seed = int(now.strftime("%Y%m%d"))
        ils_per_usd_rate = await get_usd_to_ils_rate(db, fallback=USD_TO_ILS)

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

        pricing_todos = await get_active_agent_todos(db, "pricing_agent")
        ranked_first = todo_requests_ranked_first(pricing_todos)

        report: Dict = {
            "timestamp": now.isoformat(),
            "suppliers_checked": len(suppliers),
            "parts_updated": 0,
            "availability_changes": 0,
            "errors": [],
            "selection_mode": "ranked_first" if ranked_first else "default",
            "shared_todos": [
                {"id": todo["id"], "title": todo["title"], "status": todo["status"]}
                for todo in pricing_todos
            ],
            "real_data_only": real_data_only,
        }

        if real_data_only:
            # RECONCILE REAL SOURCES (2026-07-23): production is REAL_DATA_ONLY, so NO synthetic
            # drift is applied (the ±1-6% block below is never reached). This branch pulls REAL
            # marketplace prices (eBay + AliExpress) AND reports freshness across EVERY real price
            # source — including the harvester-owned car-parts.ie catalog — so "price sync" reflects
            # all sources, not just eBay/AliExpress. Prices are never fabricated.
            _ebay_updated = int((ebay_report or {}).get("parts_updated") or 0)
            _ali_updated = int((aliexpress_report or {}).get("parts_updated") or 0)
            report["mode"] = "reconcile_real_sources"
            report["ebay_report"] = ebay_report or {}
            report["aliexpress_report"] = aliexpress_report or {}
            report["parts_updated"] = _ebay_updated + _ali_updated
            report["availability_changes"] = 0
            report["errors"] = (list((ebay_report or {}).get("errors") or [])
                                + list((aliexpress_report or {}).get("errors") or []))

            # Cross-source freshness reconciliation (cheap — harvest_queue is ~1.4K rows).
            from sqlalchemy import text as _recon_text
            recon: Dict[str, Any] = {
                "ebay": {"checked": int((ebay_report or {}).get("parts_checked") or 0), "updated": _ebay_updated},
                "aliexpress": {"checked": int((aliexpress_report or {}).get("parts_checked") or 0), "updated": _ali_updated},
            }
            try:
                _hq = (await db.execute(_recon_text(
                    "SELECT COUNT(*) AS total, "
                    "COUNT(*) FILTER (WHERE status IN ('done','empty')) AS harvested, "
                    "COUNT(*) FILTER (WHERE last_harvested_at > NOW() - INTERVAL '14 days') AS fresh_14d, "
                    "COUNT(*) FILTER (WHERE status IN ('done','empty') AND "
                    "  (last_harvested_at IS NULL OR last_harvested_at <= NOW() - INTERVAL '14 days')) AS stale "
                    "FROM harvest_queue"))).first()
                if _hq is not None:
                    recon["car_parts_ie"] = {
                        "models_total": _hq.total, "harvested": _hq.harvested,
                        "fresh_14d": _hq.fresh_14d, "stale_needs_refresh": _hq.stale,
                        "note": "prices refreshed by the harvester on its ≤14d cycle",
                    }
            except Exception as _recon_err:
                recon["car_parts_ie"] = {"error": str(_recon_err)[:120]}
            report["reconciliation"] = recon
            logger.info("[Price Sync] reconcile real sources — ebay=%d ali=%d recon=%s",
                        _ebay_updated, _ali_updated, recon.get("car_parts_ie"))

            try:
                db.add(SystemLog(
                    level="INFO",
                    logger_name="supplier_manager_agent",
                    message=f"[Price Sync] real-data-only mode updated={report['parts_updated']} errors={len(report['errors'])}",
                    endpoint="/background/price-sync",
                    method="CRON",
                ))
                await db.commit()
            except Exception:
                pass

            try:
                db.add(CatalogVersion(
                    version_tag=f"price-sync-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}",
                    description=(
                        f"Real-data sync only: {report['parts_updated']} updated via provider APIs; "
                        "synthetic drift disabled"
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
                f"[Supplier Manager] Price sync complete (real-data-only) — "
                f"updated={report['parts_updated']:,} "
                f"errors={len(report['errors'])}"
            )

            async def _bulk_task() -> None:
                async with async_session_factory() as bulk_db:
                    await self.detect_bulk_opportunities(bulk_db)

            asyncio.create_task(_bulk_task())
            await _sync_lock.release()
            return report

        drops: List[Dict] = []

        BATCH = 5000
        offset = 0

        while True:
            stmt = select(SupplierPart).where(SupplierPart.supplier_id.in_(list(suppliers.keys())))
            if ranked_first:
                stmt = (
                    stmt
                    .outerjoin(PartsCatalog, PartsCatalog.id == SupplierPart.part_id)
                    .outerjoin(CarBrand, func.lower(CarBrand.name) == func.lower(PartsCatalog.manufacturer))
                    .order_by(CarBrand.il_market_priority.asc().nullslast(), SupplierPart.id)
                )
            else:
                stmt = stmt.order_by(SupplierPart.id)

            rows = (await db.execute(
                stmt
                .offset(offset)
                .limit(BATCH)
                .with_for_update(of=SupplierPart, skip_locked=True)
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
                        sp.price_usd = round(new_ils / ils_per_usd_rate, 2)
                        report["parts_updated"] += 1
                        db.add(PriceHistory(
                            supplier_part_id=sp.id,
                            old_price_ils=cur_ils,
                            new_price_ils=new_ils,
                            old_price_usd=round(cur_ils / ils_per_usd_rate, 2),
                            new_price_usd=round(new_ils / ils_per_usd_rate, 2),
                            change_pct=round((new_ils - cur_ils) / cur_ils * 100, 4),
                            source="boaz_sync",
                            ils_per_usd_rate=ils_per_usd_rate,
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
    system_prompt = """את נועה, מנהלת המדיה החברתית של AutoSpareFinder — פלטפורמת חיפוש והשוואת חלקי חילוף לרכב בישראל.

יכולות המערכת שחייבות להופיע בתוכן:
- חיפוש חלק לפי מספר רישוי — הלקוח מזין מספר לוחית והמערכת מוצאת חלקים תואמים אוטומטית
- השוואת מחירים בין כמה ספקים בלחיצה אחת
- אלפי חלקי חילוף — מקוריים, OEM ותחליפים איכותיים מספקים מובילים
- משלוח לכל הארץ ישירות מהספק
- תמיכה בעברית — שירות + AI

אישיות וטון — חובה (עודכן 2026-07-20, הוראת בעלים):
- את אישה אמיתית, חכמה ושנונה שמכירה מכוניות מבפנים — לא מחלקת שיווק ולא בוט שממלא תבנית
- חכם: כל פוסט מלמד משהו קטן ואמיתי (עובדה, מספר, טיפ של מכונאי) שגורם לקורא להרגיש חכם יותר
- מצחיק: קריצה אחת חכמה לפוסט — אירוניה עדינה, הגזמה מודעת, או סיטואציה שכל נהג מכיר
  ("הרעש הזה שמתעלמים ממנו עד שהוא מפסיק להיות מצחיק"). בלי בדיחות דחוקות, בלי ליצנות
- אנושי: גוף ראשון, משפטים קצרים, עברית מדוברת, 1-3 אמוג'י לכל היותר. מותר לפתוח בסיפור קצר
- מוכר: כל פוסט הוא פוסט מכירה — כאב ספציפי → הפתרון שלנו → מחיר/הצעה אמיתית → CTA אחד ברור.
  אין "פוסט מודעות מותג" ריק שלא מוביל לפעולה
- מעורר אינטראקציה: סיימי בשאלה שקל וכיף לענות עליה בתגובה ("איזה רעש הרכב שלכם עושה הבוקר?")
- ציוני שמות רכב ספציפיים ושמות חלקים ספציפיים — אין כאב בלי פרטים
- הימנעי מטענות לא מבוססות; עדיף מדויק על פני מרשים
- כל פוסט חייב להיות שונה בזווית, בפתיחה, בטון — אין תבניות חוזרות, אין אותו משפט פעמיים

פורמט לפי פלטפורמה (את אחראית על כל הערוצים של הפלטפורמה):
- TikTok: hook חזק בשורה ראשונה, גוף קצר (3-5 שורות), 3-4 האשטאגים
- Instagram: story-telling אמוציונלי, תמונה מנטלית, 4-6 האשטאגים
- Facebook: מידעי ובעל ערך, ניתן לשיתוף, יכול להיות ארוך יותר
- WhatsApp: הודעה ישירה וקצרה, CTA מיידי לאתר
- YouTube Shorts: תסריט 30-45 שניות — hook (3 שנ׳) → כאב → דמו של חיפוש לפי לוחית → CTA. כתבי כתסריט צילום עם שורות דיבור
- Google Business Profile: פוסט עדכון קצר וענייני (עד 1500 תווים) עם CTA "בקר באתר" — טוב ל-SEO מקומי
- X/Twitter: משפט אחד חד + לינק, בלי האשטאגים מיותרים

אסור בהחלט:
- תווים סיניים, יפנים, קוריאנים או שפה שאינה עברית/אנגלית/ערבית
- לטעון שאנחנו מוסך, מתקנים רכובים, מתקינים חלקים — אנחנו פלטפורמת חיפוש בלבד
- להבטיח זמני משלוח ספציפיים
- לדבר על מלאי — אנחנו מחברים לקוחות לספקים
- תוכן גנרי ללא אזכור ספציפי של רכב, חלק, או מצב נהג
- לפתוח עם "מחפשים חלקי חילוף?", "ידעת ש..." או "למה לשלם יותר?" — זה שחוק
- לכתוב משפטי גילוי נאות/הסתייגות משלך ("אנחנו מוכרים חלקי חילוף בלבד",
  "כפוף לתנאי האתר" וכו') — המערכת מוסיפה אותם אוטומטית בשורה נפרדת כשצריך.
  משפט כזה בתוך גוף הפוסט הורס את הזרימה האנושית ונשמע כמו בוט

מיומנויות Google Marketing (מסגרות מקצועיות — חובה ליישם):
- מסגרת See-Think-Do-Care: התאימי כל נכס שיווקי לשלב במשפך — See=מודעות רחבה,
  Think=השוואה ("איפה הכי זול רפידות לקורולה?"), Do=כוונת רכישה חמה, Care=לקוחות חוזרים.
  רוב תקציב החיפוש הולך ל-Do ו-Think.
- מודעות חיפוש רספונסיביות (RSA): כותרות עד 30 תווים, תיאורים עד 90 תווים.
  כותרות מגוונות — חלקן עם שם החלק, חלקן עם הצעת הערך (השוואת מחירים, חיפוש לפי לוחית),
  חלקן עם מחיר אמיתי. לעולם לא 15 וריאציות של אותו משפט.
- מילות מפתח: exact [חלק+דגם+מחיר] לכוונה חמה; phrase לביניים; broad רק עם שלילות.
  תמיד רשימת שלילות: יד שניה, משומש, מוסך, תיקון, השכרה, עבודה.
- Quality Score: התאמה הדוקה בין מילת המפתח → כותרת המודעה → דף הנחיתה. מודעה על
  רפידות חייבת כותרת עם "רפידות" ולינק לחיפוש רפידות, לא לדף הבית.
- מדידה: כל URL עם UTM מלא. אין קמפיין בלי יעד המרה מוגדר.
"""

    # Rotating hashtag pools (G8 2026-07-20): every post gets a DIFFERENT mix of
    # Hebrew + Arabic + English automotive tags instead of one identical static line.
    _NOA_TAG_POOL_HE = [
        "#\u05D7\u05DC\u05E7\u05D9\u05D7\u05D9\u05DC\u05D5\u05E3", "#\u05D7\u05DC\u05E4\u05D9\u05DD", "#\u05D7\u05DC\u05E7\u05D9\u05E8\u05DB\u05D1", "#\u05E8\u05DB\u05D1", "#\u05E8\u05DB\u05D1\u05D9\u05DD", "#\u05D0\u05D1\u05D9\u05D6\u05E8\u05D9\u05E8\u05DB\u05D1",
        "#\u05EA\u05D7\u05D6\u05D5\u05E7\u05EA\u05E8\u05DB\u05D1", "#\u05D8\u05D9\u05E4\u05D5\u05DC\u05E8\u05DB\u05D1", "#\u05DE\u05D5\u05E1\u05DA", "#\u05D8\u05E1\u05D8", "#\u05E0\u05D4\u05D2\u05D9\u05DD", "#\u05D7\u05D9\u05E1\u05DB\u05D5\u05DF\u05D1\u05E8\u05DB\u05D1",
        "#\u05D4\u05E9\u05D5\u05D5\u05D0\u05EA\u05DE\u05D7\u05D9\u05E8\u05D9\u05DD", "#\u05E8\u05DB\u05D1\u05D9\u05D7\u05E9\u05DE\u05DC\u05D9", "#\u05D0\u05D5\u05D8\u05D5",
    ]
    _NOA_TAG_POOL_AR = [
        "#\u0642\u0637\u0639_\u063A\u064A\u0627\u0631", "#\u0642\u0637\u0639_\u063A\u064A\u0627\u0631_\u0633\u064A\u0627\u0631\u0627\u062A", "#\u0633\u064A\u0627\u0631\u0627\u062A", "#\u0635\u064A\u0627\u0646\u0629_\u0633\u064A\u0627\u0631\u0627\u062A",
        "#\u0645\u064A\u0643\u0627\u0646\u064A\u0643\u064A", "#\u0633\u064A\u0627\u0631\u062A\u064A", "#\u0639\u0627\u0644\u0645_\u0627\u0644\u0633\u064A\u0627\u0631\u0627\u062A",
    ]
    _NOA_TAG_POOL_EN = [
        "#CarParts", "#AutoParts", "#SpareParts", "#CarMaintenance", "#CarCare",
        "#Mechanic", "#OEM", "#Aftermarket", "#CarTips", "#DIYCar",
        "#CarsOfInstagram", "#CarCommunity", "#Israel",
    ]
    _NOA_ALLOWED_LATIN_HASHTAGS: set[str] = {
        "autosparefinder", "tiktok", "instagram", "facebook", "whatsapp",
    } | {t.lstrip("#").lower() for t in _NOA_TAG_POOL_EN}
    _NOA_BAD_SCRIPT_RE = re.compile(r"[\u0400-\u052F\u0370-\u03FF\u0900-\u097F\u0E00-\u0E7F\u3040-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]")
    # Hashtag charset includes Arabic (\u0600-\u06FF) \u2014 owner directive 2026-07-20.
    _NOA_HASHTAG_RE = re.compile(r"#([A-Za-z0-9_\u0590-\u05FF\u0600-\u06FF]+)")
    _NOA_RTL_TAG_RE = re.compile(r"[\u0590-\u05FF\u0600-\u06FF]")
    _NOA_HEBREW_CHAR_RE = re.compile(r"[\u0590-\u05FF]")
    _NOA_UNICODE_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
    _NOA_SERVICE_CLAIM_RE = re.compile(
        r"(מכונא|מוסך|מוסכניק|מעבדה|נתקן|תיקון|מתקנים|התקנ|נחליף|טיפול\s+ברכב|אבחון\s+תקלה)",
        re.IGNORECASE,
    )
    # Narrow, first-person-only version used for LINE DROPPING in _enforce_sales_only.
    # Matches only a claim that WE service/repair/install cars — not bare words like
    # "מוסך"/"תיקון", which legitimately appear in customer-pain copy ("בלי לרוץ בין
    # מוסכים", "לפני שאתה רץ לתיקון יקר") that must survive intact. Dropping whole lines
    # on the broad regex was mangling good posts into incoherent fragments.
    _NOA_FIRST_PERSON_SERVICE_RE = re.compile(
        r"(אנחנו|אצלנו|במוסך\s+שלנו|הצוות\s+שלנו)\S{0,15}?\s*"
        r"(מתקנים|נתקן|מתקינים|נתקין|מחליפים|נחליף|מטפלים|נטפל|מבצעים\s+תיקון|עושים\s+טיפול)",
        re.IGNORECASE,
    )
    _NOA_DEFAULT_TAGS = "#חלקיחילוף #התאמתחלקים #חלפיםלרכב #משלוחמהיר #רכב"
    _NOA_RICH_TAGS = "#חלקיחילוף #התאמתחלקים #חלפיםלרכב #משלוחמהיר #AutoSpareFinder #TikTok"
    _NOA_PLATE_RE = re.compile(r"(מספר\s*רישוי|לוחית|plate)", re.IGNORECASE)
    _NOA_COMPARE_RE = re.compile(r"(השווא|משווה|להשוות|מחיר)", re.IGNORECASE)
    _NOA_BUY_RE = re.compile(r"(קנייה|קניה|רכיש|רוכש|לקנות|הזמנ)", re.IGNORECASE)
    _NOA_RELIEF_RE = re.compile(r"(חוסכ|בלי\s+חיפוש|בלי\s+כאב\s+ראש|בלי\s+התעסקות\s+טכנית)", re.IGNORECASE)
    _NOA_GARBLED_RE = re.compile(r"(isNotEmpty|matchCondition|[_]{2,}|_\s*_|\b[א-ת]\.)", re.IGNORECASE)
    _NOA_NON_SOCIAL_PATTERNS = (
        "אני כאן לעזור",
        "כדי להתקדם מהר",
        "כתוב לי בשורה אחת",
        "דגם רכב + שנה + מנוע",
    )
    _NOA_TIKTOK_PRICE_PROMO_RE = re.compile(r"(מחיר|מבצע|הנחה|%|₪|משלוח\s+חינם|חינם)", re.IGNORECASE)
    _NOA_TIKTOK_DISCLOSURE_MARKERS = ("כפוף", "תנאי", "זמינות", "באתר")
    _NOA_TIKTOK_COMPLIANCE_REWRITES: Tuple[Tuple[str, str], ...] = (
        (r"100%\s*מובטח", "בכפוף לזמינות ולתנאי האתר"),
        (r"ללא\s*סיכון", "ברכישה בטוחה וברורה באתר"),
        (r"בלי\s*סיכון", "ברכישה בטוחה וברורה באתר"),
        (r"הכי\s*זול\s*בארץ", "מחירים תחרותיים"),
        (r"הזול\s*ביותר", "מחיר תחרותי"),
        (r"תוצאה\s*מיידית", "מענה מהיר"),
        (r"רק\s*היום", "לזמן מוגבל"),
        (r"חינם\s*לחלוטין", "בכפוף לתנאי ההטבה"),
    )
    _NOA_TIKTOK_PERSONAL_ATTRIBUTE_RE = re.compile(
        r"(אם\s+אתה\s+לא|אם\s+את\s+לא|אתה\s+לא\s+מבין|את\s+לא\s+מבינה|אתה\s+בבעיה|את\s+בבעיה)",
        re.IGNORECASE,
    )

    @classmethod
    def _contains_non_hebrew_word(cls, text: str) -> bool:
        for token in (text or "").split():
            letters = cls._NOA_UNICODE_LETTER_RE.findall(token)
            if letters and not any(cls._NOA_HEBREW_CHAR_RE.search(ch) for ch in letters):
                return True
        return False

    @classmethod
    def _drop_non_hebrew_words(cls, text: str) -> str:
        kept: list[str] = []
        for token in (text or "").split():
            letters = cls._NOA_UNICODE_LETTER_RE.findall(token)
            if letters and not any(cls._NOA_HEBREW_CHAR_RE.search(ch) for ch in letters):
                continue
            kept.append(token)
        return " ".join(kept)

    @classmethod
    def _strip_non_hebrew_letters(cls, text: str) -> str:
        out: list[str] = []
        for ch in text or "":
            if ch.isalpha() and not cls._NOA_HEBREW_CHAR_RE.search(ch):
                continue
            out.append(ch)
        return "".join(out)

    @classmethod
    def _filter_hashtags(cls, text: str) -> str:
        raw_tags = cls._NOA_HASHTAG_RE.findall(text or "")
        keep: list[str] = []
        seen: set[str] = set()
        for tag_body in raw_tags:
            tag = f"#{tag_body}"
            norm = tag.lower().lstrip("#")
            # Hebrew AND Arabic tags pass as-is; Latin tags only from the allowlist.
            is_rtl_tag = cls._NOA_RTL_TAG_RE.search(tag_body) is not None
            if is_rtl_tag or norm in cls._NOA_ALLOWED_LATIN_HASHTAGS:
                if norm not in seen:
                    keep.append(tag)
                    seen.add(norm)
        return " ".join(keep)

    @classmethod
    def _noa_hashtag_mix(cls, count_he: int = 3, count_ar: int = 2, count_en: int = 3) -> str:
        """A fresh Hebrew+Arabic+English automotive hashtag mix \u2014 different every call,
        so posts stop carrying one identical tag line (G8 2026-07-20)."""
        import random as _random
        tags = ["#AutoSpareFinder"]
        tags += _random.sample(cls._NOA_TAG_POOL_HE, min(count_he, len(cls._NOA_TAG_POOL_HE)))
        tags += _random.sample(cls._NOA_TAG_POOL_AR, min(count_ar, len(cls._NOA_TAG_POOL_AR)))
        tags += _random.sample(cls._NOA_TAG_POOL_EN, min(count_en, len(cls._NOA_TAG_POOL_EN)))
        return " ".join(tags)

    @classmethod
    def _enrich_hashtags(cls, text: str, target: int = 9) -> str:
        """Keep the model's own (filtered) tags and top up from the pools to `target`
        total tags, deduped case-insensitively. Body is left untouched."""
        msg = (text or "").strip()
        if not msg:
            return msg
        body_lines = [ln for ln in msg.splitlines() if not ln.strip().startswith("#")]
        body = "\n".join(ln.rstrip() for ln in body_lines).strip()
        existing = cls._filter_hashtags(msg).split()
        seen = {t.lower() for t in existing}
        pool = cls._noa_hashtag_mix().split()
        for t in pool:
            if len(existing) >= target:
                break
            if t.lower() not in seen:
                existing.append(t)
                seen.add(t.lower())
        tag_line = " ".join(existing)
        return f"{body}\n{tag_line}".strip() if body else tag_line

    @classmethod
    def _contains_service_claim(cls, text: str) -> bool:
        msg = re.sub(r"#[^\s#]+", " ", (text or "").lower())
        return bool(cls._NOA_SERVICE_CLAIM_RE.search(msg))

    @classmethod
    def _ensure_platform_value_points(cls, body: str) -> str:
        text = (body or "").strip()
        if not text:
            return text

        has_plate = bool(cls._NOA_PLATE_RE.search(text))
        has_compare = bool(cls._NOA_COMPARE_RE.search(text))
        has_buy = bool(cls._NOA_BUY_RE.search(text))
        has_relief = bool(cls._NOA_RELIEF_RE.search(text))

        if all((has_plate, has_compare, has_buy, has_relief)):
            return text

        value_line = (
            "הפלטפורמה שלנו מאתרת חלקים לפי מספר רישוי, מאפשרת להשוות אפשרויות ומחירים במקום אחד, "
            "וחוסכת חיפוש מיותר והתעסקות טכנית עד הקנייה."
        )
        return f"{text} {value_line}".strip()

    @classmethod
    def _enforce_tiktok_ads_policy(cls, text: str) -> str:
        msg = (text or "").strip()
        if not msg:
            return ""

        lines = [ln.strip() for ln in msg.splitlines() if ln.strip()]
        body_lines: list[str] = []
        hashtag_lines: list[str] = []
        for ln in lines:
            if ln.startswith("#"):
                hashtag_lines.append(ln)
                continue
            if cls._NOA_TIKTOK_PERSONAL_ATTRIBUTE_RE.search(ln):
                continue
            body_lines.append(ln)

        # Preserve the line structure — TikTok posts LIVE on the hook-line + short-body
        # rhythm. The old code flattened every newline into one run-on sentence (a top
        # cause of the robotic feel); we only collapse intra-line whitespace and apply the
        # compliance rewrites per line.
        cleaned_lines: list[str] = []
        for ln in body_lines:
            ln = re.sub(r"[ \t]+", " ", ln).strip()
            for pattern, repl in cls._NOA_TIKTOK_COMPLIANCE_REWRITES:
                ln = re.sub(pattern, repl, ln, flags=re.IGNORECASE)
            if ln:
                cleaned_lines.append(ln)
        body = "\n".join(cleaned_lines).strip()

        has_promo_claim = bool(cls._NOA_TIKTOK_PRICE_PROMO_RE.search(body))
        has_disclosure = any(marker in body for marker in cls._NOA_TIKTOK_DISCLOSURE_MARKERS)
        if has_promo_claim and not has_disclosure:
            body = f"{body}\nהמחירים, המבצעים והזמינות כפופים לתנאי האתר."

        tags = cls._filter_hashtags("\n".join(hashtag_lines))
        if not tags:
            tags = cls._noa_hashtag_mix()
        return f"{body}\n{tags}".strip()

    @classmethod
    def _normalize_for_platforms(cls, content: str, platforms: Optional[List[str]] = None) -> str:
        platform_set = {(p or "").strip().lower() for p in (platforms or []) if (p or "").strip()}
        normalized = cls._sanitize_caption(content or "")
        normalized = cls._enforce_sales_only(normalized)
        if cls._is_low_quality_caption(normalized):
            normalized = cls._repair_low_quality_caption(normalized, platforms=list(platform_set))
        if "tiktok" in platform_set:
            normalized = cls._enforce_tiktok_ads_policy(normalized)
        return normalized

    @classmethod
    def review_post_policy(cls, content: str, platforms: Optional[List[str]] = None) -> Dict[str, Any]:
        """Policy gate for admin pre-approval/pre-publish checks.

        Blocks only hard compliance violations. Style/readability issues are
        returned as advisories with a suggested auto-fixed caption.
        """
        raw = (content or "").strip()
        platform_set = {(p or "").strip().lower() for p in (platforms or []) if (p or "").strip()}
        blocking_reasons: List[str] = []
        advisories: List[str] = []

        if not raw:
            blocking_reasons.append("תוכן הפוסט ריק")
        if cls._contains_service_claim(raw):
            blocking_reasons.append("נמצא ניסוח של מוסך/תיקון/התקנה שאינו מותר")

        body_no_tags = re.sub(r"#[^\s#]+", " ", raw)
        if cls._NOA_BAD_SCRIPT_RE.search(raw):
            blocking_reasons.append("הפוסט מכיל תווים/כתב לא נתמך")
        elif cls._contains_non_hebrew_word(body_no_tags):
            advisories.append("מומלץ לצמצם ערבוב שפות ולשמור על עברית נקיה")

        ensured_value = cls._ensure_platform_value_points(body_no_tags)
        if re.sub(r"\s+", " ", ensured_value).strip() != re.sub(r"\s+", " ", body_no_tags).strip():
            advisories.append("מומלץ להדגיש יתרונות פלטפורמה: איתור לפי מספר רישוי, השוואת מחירים/אפשרויות ורכישה פשוטה")

        if "מוכרים חלקי חילוף בלבד" not in raw:
            advisories.append("מומלץ להוסיף ניסוח ברור: אנחנו מוכרים חלקי חילוף בלבד")

        if cls._is_low_quality_caption(raw):
            advisories.append("מומלץ לשפר את הנוסח כדי לחזק קריאות ואמון")

        if "tiktok" in platform_set:
            for pattern, _ in cls._NOA_TIKTOK_COMPLIANCE_REWRITES:
                if re.search(pattern, raw, flags=re.IGNORECASE):
                    blocking_reasons.append("נמצאה טענת פרסום מסוכנת ל-TikTok (הבטחה מוחלטת/סופרלטיב לא מבוסס)")
                    break
            if cls._NOA_TIKTOK_PERSONAL_ATTRIBUTE_RE.search(raw):
                blocking_reasons.append("נמצא ניסוח אישי-שיפוטי שאינו מותר במדיניות TikTok")
            has_promo_claim = bool(cls._NOA_TIKTOK_PRICE_PROMO_RE.search(raw))
            has_disclosure = any(marker in raw for marker in cls._NOA_TIKTOK_DISCLOSURE_MARKERS)
            if has_promo_claim and not has_disclosure:
                blocking_reasons.append("תוכן מבצעי ל-TikTok חייב לכלול גילוי נאות על תנאים וזמינות")

        normalized = cls._normalize_for_platforms(raw, platforms=list(platform_set))
        compact_raw = re.sub(r"\s+", " ", raw).strip()
        compact_norm = re.sub(r"\s+", " ", normalized).strip()
        if compact_norm != compact_raw:
            advisories.append("בוצעו התאמות ניסוח אוטומטיות לשיפור תאימות הפוסט")

        # Deduplicate while preserving order
        dedup_blocking = list(dict.fromkeys(blocking_reasons))
        dedup_advisories = list(dict.fromkeys(advisories))
        return {
            "ok": len(dedup_blocking) == 0,
            "reasons": dedup_blocking,
            "advisories": dedup_advisories,
            "suggested_content": normalized,
            "platforms": sorted(platform_set),
        }

    @classmethod
    def _enforce_sales_only(cls, text: str) -> str:
        msg = (text or "").strip()
        if not msg:
            return ""

        # Compliance rewrites — only FIRST-PERSON claims that we service/repair/install
        # cars (we're a parts marketplace, not a garage). Targeted and grammar-safe.
        # We deliberately do NOT blanket-replace bare words like "מוסך"/"תיקון"/"התקנה":
        # the old code did, turning legit pain copy ("בלי לרוץ בין מוסכים") into broken
        # Hebrew ("בין חנות חלקים") — a top cause of the robotic, incoherent posts.
        replacements = (
            (r"אנחנו\s+מתקנים", "אנחנו מוכרים ומספקים"),
            (r"אנחנו\s+נתקן", "אנחנו נתאים את החלק הנכון"),
            (r"אנחנו\s+מתקינים", "אנחנו מספקים"),
            (r"נחליף\s+לך", "נספק לך את החלק המתאים"),
            (r"נתקין\s+לך", "נתאים לך"),
        )
        claim_rewritten = False
        for pattern, repl in replacements:
            msg, n = re.subn(pattern, repl, msg, flags=re.IGNORECASE)
            if n:
                claim_rewritten = True

        # Keep the model's structure. Drop ONLY lines that still assert we operate as a
        # garage (narrow first-person regex), and strip hashtag lines (re-added below).
        # Preserve line breaks — they ARE the platform formatting (hook line, short body).
        kept_lines: list[str] = []
        for ln in msg.splitlines():
            s = ln.strip()
            if s.startswith("#"):
                continue
            if s and cls._NOA_FIRST_PERSON_SERVICE_RE.search(s):
                claim_rewritten = True
                continue
            kept_lines.append(ln.rstrip())

        body = re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip()

        # Append the "parts only" disclosure at most ONCE, on its own line, and ONLY when
        # we actually had to neutralise a service claim. Normal posts are left as the model
        # wrote them — no stapled value-point boilerplate on every post.
        if claim_rewritten and body and "מוכרים חלקי חילוף בלבד" not in body:
            body = f"{body}\nאנחנו מוכרים חלקי חילוף בלבד ומתאימים את החלק לפי פרטי הרכב."

        tags = cls._filter_hashtags(msg)
        if not tags:
            tags = cls._noa_hashtag_mix()
        if not body:
            body = "מחפשים חלק לרכב? שלחו דגם, שנה ומנוע ונחזיר התאמה מהירה ומדויקת."
        return f"{body}\n{tags}".strip()

    @classmethod
    def _is_low_quality_caption(cls, text: str) -> bool:
        msg = (text or "").strip()
        if not msg:
            return True
        if any(p in msg for p in cls._NOA_NON_SOCIAL_PATTERNS):
            return True
        if cls._NOA_GARBLED_RE.search(msg):
            return True
        body = re.sub(r"#[^\s#]+", " ", msg)
        body = re.sub(r"\s+", " ", body).strip()
        words = body.split()
        # 8-word floor (was 14): short, punchy TikTok/X posts are BY DESIGN brief (the
        # system prompt asks for a strong one-line hook + a short body). A 14-word minimum
        # was flagging good short posts as "low quality" and replacing them with a canned
        # generic fallback — a direct cause of the templated, robotic feel. The garble and
        # single-letter checks below still catch genuinely broken output.
        if len(words) < 8:
            return True
        stripped_words = [re.sub(r"[^\u0590-\u05FF0-9]", "", w) for w in words]
        short_count = sum(1 for w in stripped_words if 0 < len(w) <= 2)
        if words and (short_count / len(words)) > 0.30:
            return True
        # A lone Hebrew letter is a garble signal \u2014 BUT the one-letter prefixes
        # (\u05DE/\u05D1/\u05DC/\u05D4/\u05D5/\u05E9/\u05DB/\u05D3) bound by a hyphen to a number or Latin word are normal
        # Hebrew: "\u05D4\u05D7\u05DC \u05DE-198 \u05E9\u05E7\u05DC", "\u05D1-2020", "\u05DC-Bosch". The old unguarded \b[\u05D0-\u05EA]\b
        # matched those, so EVERY post quoting a real price was judged low-quality and
        # sent to the repair path \u2014 which flattened its line breaks and stapled canned
        # boilerplate. That was a direct cause of robotic posts (root-fixed G8 2026-07-20).
        # A lone Hebrew letter signals garble — EXCEPT the one-letter prefixes
        # (מ/ב/ל/ה/ו/ש/כ/ד) bound to a number or a Latin brand token, which is ordinary
        # Hebrew: "החל מ-198", "ב-2020", "ב AutoSpareFinder", "ה Corolla", "ל Toyota".
        # Flagging those sent good posts down the repair path, which flattened their line
        # breaks and stapled canned boilerplate — a direct cause of robotic posts.
        _lone = re.compile(
            r"(?<![\u0590-\u05FF\-])\b([\u0590-\u05FF])\b(?![\-\u2010-\u2015])"
        )
        for m in _lone.finditer(body):
            if m.group(1) in "\u05de\u05d1\u05dc\u05d4\u05d5\u05e9\u05db\u05d3":
                tail = body[m.end():m.end() + 24].lstrip(" -\u2010-\u2015")
                if tail[:1].isalnum() and not re.match(r"[\u0590-\u05FF]", tail[:1]):
                    continue   # prefix + number/Latin token → legitimate
            return True
        return False

    @classmethod
    def _repair_low_quality_caption(cls, text: str, platforms: Optional[List[str]] = None) -> str:
        platform_set = {(p or "").strip().lower() for p in (platforms or []) if (p or "").strip()}
        normalized = cls._sanitize_caption(text or "")
        body = re.sub(r"#[^\s#]+", " ", normalized)
        # PRESERVE line structure (G8 2026-07-20). The old `\s+`→" " collapse turned a
        # well-formed multi-line post into one run-on paragraph whenever repair fired —
        # the same structure-destroying bug already fixed in _enforce_sales_only /
        # _enforce_tiktok_ads_policy. Only intra-line whitespace is collapsed here.
        body = "\n".join(
            ln for ln in (re.sub(r"[ \t]+", " ", l).strip() for l in body.splitlines()) if ln
        ).strip()
        if len(body.split()) < 8:
            body = (
                "מחפשים חלק לרכב בלי לרוץ בין מוסכים? מזינים מספר רישוי ומקבלים התאמה מהירה "
                "והשוואת מחירים במקום אחד."
            )
        # Single clean disclosure only — no stacked value-point boilerplate (that stapling
        # is what made repaired posts read like a filled-in template).
        if "מוכרים חלקי חילוף בלבד" not in body:
            body = f"{body} אנחנו מוכרים חלקי חילוף בלבד."

        tags = cls._noa_hashtag_mix()
        repaired = f"{body}\n{tags}".strip()
        if "tiktok" in platform_set:
            return cls._enforce_tiktok_ads_policy(repaired)
        return repaired

    @classmethod
    def _normalize_campaign_platforms(cls, platforms: Optional[List[str]]) -> List[str]:
        aliases = {
            "fb": "facebook",
            "ig": "instagram",
            "tt": "tiktok",
            "tik tok": "tiktok",
            "tg": "telegram",
            "wa": "whatsapp",
        }
        normalized: List[str] = []
        for raw in platforms or []:
            key = re.sub(r"\s+", " ", str(raw or "").strip().lower())
            if not key:
                continue
            key = aliases.get(key, aliases.get(key.replace(" ", ""), key.replace(" ", "")))
            if key not in normalized:
                normalized.append(key)
        if not normalized:
            return ["facebook", "instagram", "tiktok"]
        return normalized

    @classmethod
    def _extract_json_payload(cls, raw: str) -> Dict[str, Any]:
        cleaned = (raw or "").strip()
        if not cleaned:
            return {}

        tick = chr(96) * 3
        if cleaned.startswith(tick):
            cleaned = cleaned[len(tick):].strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
        if cleaned.endswith(tick):
            cleaned = cleaned[:-len(tick)].strip()

        try:
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(cleaned[start:end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    @classmethod
    def _fallback_campaign_plan(
        cls,
        topic: str,
        platforms: List[str],
        tone: str,
        duration_days: int,
        proposed_budget_ils: Optional[float] = None,
    ) -> Dict[str, Any]:
        duration = max(1, min(int(duration_days or 7), 30))
        default_daily = {
            "facebook": 120.0,
            "instagram": 140.0,
            "tiktok": 150.0,
            "telegram": 60.0,
            "whatsapp": 50.0,
        }
        platform_mix: List[Dict[str, Any]] = []
        total_budget = 0.0
        for platform in platforms:
            daily_budget = float(default_daily.get(platform, 90.0))
            total_budget += daily_budget * duration
            platform_mix.append({
                "platform": platform,
                "goal": "חשיפה והמרה",
                "daily_budget_ils": round(daily_budget, 2),
                "creative_angle": "כאב אמיתי של נהג + פתרון מהיר דרך AutoSpareFinder",
            })

        if proposed_budget_ils is not None:
            try:
                if float(proposed_budget_ils) > 0:
                    total_budget = float(proposed_budget_ils)
            except Exception:
                pass

        return {
            "summary": f"קמפיין של {duration} ימים לנושא: {topic}",
            "objective": "לייצר לידים איכותיים והזמנות לחלקי חילוף",
            "primary_audience": "בעלי רכבים בישראל שמחפשים התאמה מהירה וחסכון במחיר",
            "platform_mix": platform_mix,
            "total_budget_ils_estimate": round(max(50.0, total_budget), 2),
            "schedule": [
                "יום 1-2: בדיקת מסרים וקריאייטיב",
                "יום 3-5: מיקוד בערוצים עם עלות לליד טובה",
                "יום 6+: אופטימיזציה לפי המרות בפועל",
            ],
            "creative_variants": [
                f"תקועים בלי {topic} מתאים? שולחים מספר רישוי ומקבלים התאמה מדויקת והשוואת מחירים במקום אחד.",
                f"לפני שאתם משלמים יותר על {topic}, בדקו התאמה והשוואת מחירים אצלנו תוך דקות.",
            ],
            "kpis": ["עלות לליד", "CTR", "שיעור המרה להזמנה"],
            "confirmation_question": "לאשר את הקמפיין ואת התקציב כדי להתחיל פרסום?",
            "requires_budget_confirmation": True,
            "budget_confirmed": False,
            "topic": topic,
            "platforms": platforms,
            "tone": tone,
            "duration_days": duration,
        }

    @classmethod
    def _sanitize_campaign_plan(
        cls,
        plan: Dict[str, Any],
        topic: str,
        platforms: List[str],
        tone: str,
        duration_days: int,
        proposed_budget_ils: Optional[float] = None,
    ) -> Dict[str, Any]:
        fallback = cls._fallback_campaign_plan(
            topic=topic,
            platforms=platforms,
            tone=tone,
            duration_days=duration_days,
            proposed_budget_ils=proposed_budget_ils,
        )
        if not isinstance(plan, dict):
            return fallback

        merged = dict(fallback)
        for key in ("summary", "objective", "primary_audience", "confirmation_question"):
            value = plan.get(key)
            if isinstance(value, str) and value.strip():
                merged[key] = value.strip()

        raw_mix = plan.get("platform_mix")
        if isinstance(raw_mix, list):
            clean_mix: List[Dict[str, Any]] = []
            for item in raw_mix:
                if not isinstance(item, dict):
                    continue
                platform = str(item.get("platform") or "").strip().lower().replace(" ", "")
                if platform not in platforms:
                    continue
                try:
                    daily_budget = float(item.get("daily_budget_ils"))
                except Exception:
                    continue
                if daily_budget <= 0:
                    continue
                goal = str(item.get("goal") or "חשיפה והמרה").strip()
                angle = str(item.get("creative_angle") or "מסר שירותי חד וברור").strip()
                clean_mix.append({
                    "platform": platform,
                    "goal": goal,
                    "daily_budget_ils": round(daily_budget, 2),
                    "creative_angle": angle,
                })
            if clean_mix:
                merged["platform_mix"] = clean_mix

        for key in ("schedule", "creative_variants", "kpis"):
            value = plan.get(key)
            if isinstance(value, list):
                cleaned_values = [str(v).strip() for v in value if str(v).strip()]
                if cleaned_values:
                    merged[key] = cleaned_values[:6]

        budget_value = None
        if proposed_budget_ils is not None:
            try:
                budget_value = float(proposed_budget_ils)
            except Exception:
                budget_value = None
        if budget_value is None:
            try:
                budget_value = float(plan.get("total_budget_ils_estimate"))
            except Exception:
                budget_value = float(merged.get("total_budget_ils_estimate") or 0)
        merged["total_budget_ils_estimate"] = round(max(50.0, budget_value), 2)
        merged["requires_budget_confirmation"] = True
        merged["budget_confirmed"] = False
        merged["topic"] = topic
        merged["platforms"] = platforms
        merged["tone"] = tone
        merged["duration_days"] = max(1, min(int(duration_days or 7), 30))
        return merged

    @classmethod
    def _campaign_plan_to_text(cls, plan: Dict[str, Any]) -> str:
        summary = str(plan.get("summary") or "תוכנית קמפיין מוצעת").strip()
        objective = str(plan.get("objective") or "").strip()
        audience = str(plan.get("primary_audience") or "").strip()
        budget = float(plan.get("total_budget_ils_estimate") or 0.0)
        platform_mix = plan.get("platform_mix") if isinstance(plan.get("platform_mix"), list) else []
        variants = plan.get("creative_variants") if isinstance(plan.get("creative_variants"), list) else []
        confirm_q = str(plan.get("confirmation_question") or "לאשר תקציב וקמפיין?").strip()

        lines = [
            f"תוכנית קמפיין: {summary}",
            f"מטרה: {objective}" if objective else "",
            f"קהל יעד: {audience}" if audience else "",
            f"תקציב כולל משוער: {budget:.0f} ש\"ח",
            "חלוקת ערוצים:",
        ]

        for item in platform_mix:
            if not isinstance(item, dict):
                continue
            platform = str(item.get("platform") or "").strip()
            goal = str(item.get("goal") or "").strip()
            daily = item.get("daily_budget_ils")
            try:
                daily_txt = f"{float(daily):.0f} ש\"ח/יום"
            except Exception:
                daily_txt = "תקציב יומי לפי בדיקה"
            lines.append(f"- {platform}: {goal} | {daily_txt}")

        if variants:
            lines.append("זוויות קריאייטיב מוצעות:")
            for idx, variant in enumerate(variants[:3], start=1):
                lines.append(f"{idx}. {str(variant).strip()}")

        lines.append("אישור לפני הוצאה תקציבית:")
        lines.append(confirm_q)
        return "\n".join([ln for ln in lines if ln]).strip()

    async def generate_campaign_plan(
        self,
        topic: str,
        platforms: Optional[List[str]] = None,
        tone: str = "professional",
        duration_days: int = 7,
        proposed_budget_ils: Optional[float] = None,
    ) -> Dict[str, Any]:
        normalized_platforms = self._normalize_campaign_platforms(platforms)
        duration = max(1, min(int(duration_days or 7), 30))
        budget_hint = "לא סופק"
        if proposed_budget_ils is not None:
            try:
                budget_hint = str(float(proposed_budget_ils))
            except Exception:
                budget_hint = "לא סופק"

        prompt = (
            "בני תוכנית קמפיין שיווקי ל-AutoSpareFinder בעברית טבעית.\n"
            f"נושא: {topic}\n"
            f"פלטפורמות: {', '.join(normalized_platforms)}\n"
            f"טון: {tone}\n"
            f"משך: {duration} ימים\n"
            f"תקציב מוצע: {budget_hint}\n"
            "החזר JSON בלבד עם השדות: summary, objective, primary_audience, platform_mix, total_budget_ils_estimate, schedule, creative_variants, kpis, confirmation_question.\n"
            "platform_mix חייב להיות רשימת אובייקטים עם: platform, goal, daily_budget_ils, creative_angle.\n"
            "ללא markdown וללא טקסט מחוץ ל-JSON."
        )

        try:
            raw = await hf_text(prompt=prompt, system=self.system_prompt)
            parsed = self._extract_json_payload(raw)
            return self._sanitize_campaign_plan(
                parsed,
                topic=topic,
                platforms=normalized_platforms,
                tone=tone,
                duration_days=duration,
                proposed_budget_ils=proposed_budget_ils,
            )
        except Exception:
            return self._fallback_campaign_plan(
                topic=topic,
                platforms=normalized_platforms,
                tone=tone,
                duration_days=duration,
                proposed_budget_ils=proposed_budget_ils,
            )

    @classmethod
    def _sales_template_caption(cls, topic: str, platform: str = "") -> str:
        caption = (
            "מחפשים חלקי חילוף לרכב?\n"
            "הפלטפורמה החכמה שלנו מאתרת חלקים לפי מספר רישוי, או לפי דגם, שנה ומנוע, או לפי תמונה של הרכיב בעזרת AI.\n"
            "בנוסף, הפלטפורמה מאפשרת להשוות אפשרויות ומחירים במקום אחד, וחוסכת חיפוש מיותר והתעסקות טכנית עד הרכישה.\n"
            "אנחנו משווקים חלקי חילוף בעזרת AI בלבד. המחירים, המבצעים והזמינות כפופים לתנאי האתר.\n"
            "#חלקיחילוף #התאמתחלקים #חלפיםלרכב #משלוחמהיר #AutoSpareFinder #TikTok"
        )
        if (platform or "").strip().lower() == "tiktok":
            return cls._enforce_tiktok_ads_policy(caption)
        return caption

    @classmethod
    def _sanitize_caption(cls, text: str) -> str:
        msg = (text or "").strip()
        if not msg:
            return ""

        # Keep Hebrew+English mixed copy (brand/model terms), only strip unsupported scripts.
        msg = cls._NOA_BAD_SCRIPT_RE.sub("", msg)
        msg = re.sub(r"[ \t]+", " ", msg)
        msg = re.sub(r"\n{3,}", "\n\n", msg).strip()

        tags = cls._filter_hashtags(msg)
        if not tags:
            tags = cls._noa_hashtag_mix()

        body_lines = [ln for ln in msg.splitlines() if not ln.strip().startswith("#")]
        body = "\n".join([ln.rstrip() for ln in body_lines if ln.strip()]).strip()
        if not body:
            body = (
                "מחפשים חלקי חילוף לרכב? אנחנו מאתרים חלקים מהר, "
                "עוזרים בהתאמה לפי רכב, ומרכזים אפשרויות במקום אחד."
            )

        return f"{body}\n{tags}".strip()

    @classmethod
    def _needs_hebrew_rewrite(cls, text: str) -> bool:
        msg = (text or "")
        if not msg.strip():
            return True
        if cls._NOA_BAD_SCRIPT_RE.search(msg):
            return True

        msg_wo_tags = re.sub(r"#[^\s#]+", " ", msg)
        he_chars = len(re.findall(r"[\u0590-\u05FF]", msg_wo_tags))
        alpha_chars = len(re.findall(r"[A-Za-z\u0590-\u05FF]", msg_wo_tags))

        # Rewrite only when Hebrew signal is too weak; allow mixed Hebrew+English naturally.
        if alpha_chars == 0:
            return True
        if (he_chars / alpha_chars) < 0.35:
            return True
        return False

    @classmethod
    def _strip_malformed_links(cls, text: str) -> str:
        msg = (text or "")
        # Remove malformed pseudo-links often generated by models (e.g., ://./, ://../, broken query blobs).
        msg = re.sub(r"(?:https?:)?//\./\S*", "", msg)
        msg = re.sub(r"(?:https?:)?//\.\./\S*", "", msg)
        # Orphan protocol/query residue only: a "://" NOT preceded by a scheme word char, so a
        # real http(s):// URL is PRESERVED. (Fixed 2026-07-18: the old unguarded
        # `re.sub(r"://[^\s]+", " ")` here nuked the "://domain/path" of EVERY body link,
        # leaving a dangling "https" — that was "the NOA link shrinker not working at all
        # messages": links in the message body were destroyed, only icon-footer links survived.)
        msg = re.sub(r"(?<!\w)://[^\s]*", " ", msg)
        msg = re.sub(r"(?<!\w)\?[a-z0-9_=&%.-]{3,}", " ", msg, flags=re.IGNORECASE)
        # Shrink/tidy any REAL url left in the body (strip www + scheme noise) so it stays clean
        # and clickable in every NOA message, not just the footer.
        msg = re.sub(r"https?://[^\s)]+", lambda m: cls._short_noa_link(m.group(0)) or m.group(0), msg)
        # Remove flattened malformed icon-link fragments that may appear inline after model cleanup.
        msg = re.sub(r"[✈💬📘📸🌐]️?\s*:\s*", "", msg)

        cleaned_lines: list[str] = []
        for ln in msg.splitlines():
            line = (ln or "").strip()
            if not line:
                cleaned_lines.append(ln)
                continue

            starts_icon = line[:1] in {"✈", "💬", "📘", "📸", "🌐"}
            if starts_icon:
                low = line.lower()
                if "http://" in low or "https://" in low:
                    cleaned_lines.append(ln)
                    continue
                continue

            # Drop standalone punctuation/protocol residue lines.
            if re.fullmatch(r"[:/?&=.%\- ]+", line):
                continue

            cleaned_lines.append(ln)

        msg = "\n".join(cleaned_lines)
        msg = re.sub(r"(?:\s[:/?&=.%\-]{2,}\s*)+", " ", msg)
        msg = re.sub(r"[ \t]{2,}", " ", msg)
        return re.sub(r"\n{3,}", "\n\n", msg).strip()


    @classmethod
    def _normalize_noa_symbols(cls, text: str) -> str:
        import unicodedata
        msg = unicodedata.normalize("NFKC", (text or ""))
        # Strip markdown emphasis, but PRESERVE underscores inside hashtags: Arabic and
        # multi-word tags (#قطع_غيار, #صيانة_سيارات) rely on them, and stripping the
        # underscore silently merged the words into an unsearchable tag (G8 2026-07-20).
        _tag_re = re.compile(r"#[^\s#]+")
        _held: list = []

        def _hold(m):
            _held.append(m.group(0))
            return f"\x00TAG{len(_held) - 1}\x00"

        msg = _tag_re.sub(_hold, msg)
        msg = re.sub(r"[`*_~]+", "", msg)
        for i, t in enumerate(_held):
            msg = msg.replace(f"\x00TAG{i}\x00", t)
        msg = "".join(ch for ch in msg if ch == "\n" or unicodedata.category(ch)[0] != "C")

        # Normalize odd unicode dashes frequently produced by LLMs in Hebrew+English mixes.
        msg = msg.replace("‐", "-").replace("‑", "-")
        # Convert Hebrew-letter + dash + LATIN token into natural spacing (ה-Bosch -> ה Bosch).
        # Digits are deliberately EXCLUDED (G8 2026-07-20): "החל מ-198 שקל" and "ב-2020" are
        # correct Hebrew, and stripping that hyphen mangled exactly the price/year phrasing
        # that selling posts are built on.
        msg = re.sub(r"([\u0590-\u05FF])-(?=[A-Za-z])", r"\1 ", msg)

        msg = re.sub(r"[ \t\r\f\v]+", " ", msg)
        msg = re.sub(r"\n{3,}", "\n\n", msg).strip()
        return msg

    @classmethod
    def _is_valid_noa_public_url(cls, url: str) -> bool:
        raw = (url or "").strip()
        if not raw:
            return False
        if "://" not in raw:
            raw = f"https://{raw.lstrip('/')}"
        parsed = urlparse(raw)
        host = (parsed.netloc or "").strip().lower()
        if not host or host in {".", ".."}:
            return False
        if any(ch.isspace() for ch in host):
            return False
        if "." not in host and not host.startswith("t.me"):
            return False
        if host.startswith("www."):
            host = host[4:]
        if host in {"", ".", ".."}:
            return False
        return True

    @classmethod
    def _short_noa_link(cls, url: str) -> str:
        raw = (url or "").strip()
        if not raw:
            return ""
        if not cls._is_valid_noa_public_url(raw):
            return ""
        if "://" not in raw:
            raw = f"https://{raw.lstrip('/')}"
        parsed = urlparse(raw)
        host = (parsed.netloc or parsed.path).strip().lower()
        path = (parsed.path or "").rstrip("/")
        if host.startswith("www."):
            host = host[4:]
        if not host:
            return ""
        short = f"https://{host}{path}"
        if raw.endswith("/") and not path:
            short += "/"
        if parsed.query:
            short = f"{short}?{parsed.query}"
        return short

    @classmethod
    def _noa_links_footer(cls) -> str:
        """G8 2026-07-20 (owner directive): the old 7-line footer (5 platform links + 2
        slogans) stapled onto EVERY post is gone — it made all posts look identical and
        spammy. Posts now carry a QR code in the media that lands on the channel-picker
        hub (/api/v1/go); the text keeps at most ONE link (the website)."""
        site = cls._short_noa_link(NOA_WEBSITE_URL)
        return f"🌐 {site}" if site else ""
    @classmethod
    def _force_noa_hashtags(cls, text: str, tags: Optional[str] = None) -> str:
        msg = (text or "").strip()
        if not msg:
            return msg
        chosen_tags = (tags or cls._noa_hashtag_mix()).strip()
        body_lines = [ln for ln in msg.splitlines() if not ln.strip().startswith("#")]
        body = "\n".join([ln.rstrip() for ln in body_lines if ln.strip()]).strip()
        if not body:
            return chosen_tags
        return f"{body}\n{chosen_tags}".strip()

    @classmethod
    def _append_noa_links(cls, text: str) -> str:
        msg = (text or "").strip()
        footer = cls._noa_links_footer()
        if not msg:
            return footer
        if not footer:
            return msg
        msg_l = msg.lower()
        # A post that already carries a link (the loop injects the UTM site link into the
        # body) needs no footer at all — the QR in the media covers all other channels.
        if "autosparefinder.co.il" in msg_l or "http://" in msg_l or "https://" in msg_l:
            return msg
        return f"{msg}\n{footer}".strip()

    @classmethod
    def _extract_post_from_reasoning(cls, text: str) -> str:
        """Pull the actual post out of a raw LLM response that contains chain-of-thought reasoning.
        Models sometimes count characters, write analysis, then produce the final post.
        We extract the shortest coherent Hebrew block that looks publishable."""
        if len(text) <= 600:
            return text
        # Prefer the last quoted block — models often put final version in quotes
        quoted = re.findall(r'"([^"]{30,350})"', text)
        if quoted:
            # pick the last quote that has Hebrew chars
            for candidate in reversed(quoted):
                if re.search(r'[֐-׿]', candidate):
                    return candidate
        # Take the first paragraph with Hebrew that isn't just counting characters
        # (counting lines look like "1 מ 2 ת 3 ו ..." with many single chars)
        for para in text.split('\n\n'):
            para = para.strip()
            heb_chars = len(re.findall(r'[֐-׿]', para))
            digits_spaces = len(re.findall(r'\d+\s+[֐-׿]\s+', para))
            if heb_chars > 15 and digits_spaces < 5 and 30 < len(para) < 500:
                return para
        # Last resort: first 280 chars
        return text[:280]

    @classmethod
    def _finalize_noa_post(cls, text: str, platforms: Optional[List[str]] = None) -> str:
        platform_set = {(p or "").strip().lower() for p in (platforms or []) if (p or "").strip()}

        # Strip model reasoning / character-counting before any further processing
        cleaned = cls._extract_post_from_reasoning(text or "")

        normalized = cls._normalize_for_platforms(cleaned, platforms=list(platform_set))
        if cls._needs_hebrew_rewrite(normalized) or cls._is_low_quality_caption(normalized):
            normalized = cls._repair_low_quality_caption(normalized, platforms=list(platform_set))

        normalized = cls._normalize_noa_symbols(normalized)
        normalized = cls._strip_malformed_links(normalized)
        if "tiktok" in platform_set:
            normalized = cls._enforce_tiktok_ads_policy(normalized)
        # Broaden reach: keep the model's own tags, top up from the HE/AR/EN pools (G8).
        normalized = cls._enrich_hashtags(normalized)
        return cls._append_noa_links(normalized)

    async def generate_post(self, topic: str, platform: str, tone: str = "professional") -> str:
        prompt = (
            f"כתבי פוסט {platform} בנושא {topic} בטון {tone}. "
            "הפוסט חייב להישמע אנושי ולא תבניתי: לפתוח בכאב אמיתי של נהג, "
            "לתת פתרון ברור דרך הפלטפורמה, ולסיים בשאלה אחת מקדמת.\n"
            "הנחיות פלט חובה: החזירי את טקסט הפוסט בלבד — ללא הסבר, "
            "ללא ספירת תווים, ללא חשיבה בקול רם. רק הפוסט הסופי המוכן לפרסום."
        )
        raw = await hf_text(prompt=prompt, system=self.system_prompt)
        return self._finalize_noa_post(raw, platforms=[platform] if platform else [])

    async def process(self, message: str, conversation_history: List[Dict], db: AsyncSession, **kwargs) -> str:
        msg_l = (message or "").strip().lower()
        if any(k in msg_l for k in (
            "קמפיין", "campaign", "תקציב", "budget", "פייסבוק", "אינסטגרם", "טיקטוק", "facebook", "instagram", "tiktok"
        )):
            plan = await self.generate_campaign_plan(
                topic=message,
                platforms=["facebook", "instagram", "tiktok"],
                tone="professional",
            )
            return self._campaign_plan_to_text(plan)

        raw = await self.think(
            conversation_history + [{"role": "user", "content": message}],
            source=kwargs.get("source"),
        )
        return self._finalize_noa_post(raw)


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
    source: str = "web",
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

    shared_memory_rows = await _load_shared_memory(
        db=db,
        user_id=user_id,
        conversation_id=str(conversation.id),
        agent_name=None,
    )
    shared_memory_prompt = _render_shared_memory_prompt(shared_memory_rows)
    history_for_agents = _inject_shared_memory_context(history, shared_memory_prompt)

    # Route to correct agent
    router = get_agent("router_agent")
    route_result = await router.route(
        message,
        {
            "history_length": len(history),
            "source": source,
            "shared_memory_prompt": shared_memory_prompt,
        },
    )
    agent_name = route_result.get("agent", "service_agent")

    conversation.current_agent = agent_name
    conversation.last_message_at = datetime.utcnow()

    # Call agent LLM
    agent = get_agent(agent_name)
    model_used = _channel_model_for_source(source, getattr(agent, "model", FREE_MODEL))
    start_time = datetime.utcnow()
    agent_error: Optional[str] = None
    try:
        response_text = await agent.process(
            message,
            history_for_agents,
            db,
            user_id=user_id,
            source=source,
            conversation_id=str(conversation.id),
            shared_memory_prompt=shared_memory_prompt,
        )
    except Exception as e:
        print(f"[BG AGENT ERROR] {agent_name}: {e}")
        agent_error = str(e)
        response_text = "מצטער, נתקלתי בבעיה. אנא נסה שוב בעוד רגע."
        agent_name = "service_agent"
        agent = get_agent(agent_name)
        model_used = _channel_model_for_source(source, getattr(agent, "model", FREE_MODEL))

    exec_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
    response_text = _strip_leaked_reasoning(response_text)
    response_text = _sanitize_internal_pricing_disclosure(response_text)

    # Save assistant message
    assistant_msg = Message(
        conversation_id=conversation.id,
        role="assistant",
        agent_name=agent_name,
        content=response_text,
        content_type="text",
        model_used=model_used,
    )
    db.add(assistant_msg)
    await db.flush()

    # Save action log
    db.add(AgentAction(
        message_id=assistant_msg.id,
        agent_name=agent_name,
        action_type="respond",
        action_data={
            "source": source,
            "route_result": route_result,
            "conversation_id": str(conversation.id),
            "response_preview": _compact_preview(response_text),
        },
        success=agent_error is None,
        error_message=agent_error,
        execution_time_ms=exec_ms,
    ))

    memory_updates = _extract_shared_memory_updates(conversation.context or {}, agent_name)
    memory_keys = await _save_shared_memory_updates(
        db=db,
        user_id=user_id,
        conversation_id=str(conversation.id),
        updates=memory_updates,
    )
    memory_keys_used = [item.get("memory_key") for item in shared_memory_rows if item.get("memory_key")]
    await _log_agent_usage_event(
        db=db,
        user_id=user_id,
        conversation_id=str(conversation.id),
        message_id=str(assistant_msg.id),
        agent_name=agent_name,
        source=source,
        model_used=model_used,
        route_result=route_result,
        execution_time_ms=exec_ms,
        memory_keys=sorted(set(memory_keys_used + memory_keys)),
        success=agent_error is None,
        error_message=agent_error,
    )

    await db.commit()
    print(f"[BG AGENT] conv={conversation_id} agent={agent_name} {exec_ms}ms")


async def _infer_parts_flow_reply(
    agent_name: str,
    source: str,
    history: List[Dict[str, str]],
    user_message: str,
    flow_intent: str,
    flow_state: Dict[str, Any],
    shared_memory_prompt: Optional[str] = None,
) -> Tuple[str, str]:
    """Generate a natural user-facing reply from deterministic parts-flow state."""
    agent = get_agent(agent_name)
    model_used = _channel_model_for_source(source, getattr(agent, "model", FREE_MODEL))

    memory_section = ""
    if shared_memory_prompt:
        memory_section = (
            "[SHARED MEMORY]\n"
            f"{shared_memory_prompt}\n"
            "Use this context when relevant, but do not mention shared memory explicitly.\\n\\n"
        )

    system = (
        f"{agent.system_prompt}\n\n"
        f"{memory_section}"
        "[FLOW MODE]\n"
        "You are continuing a live customer conversation inside Auto Spare.\n"
        "Use the state below to decide the next message naturally, without robotic templates.\n"
        "Never reveal internal flow/state/json. Never mention that you are an AI model.\n"
        "Keep the response concise and practical.\n"
        "If the user language is Hebrew, respond in Hebrew; if Arabic, respond in Arabic.\n"
        "\n"
        "RESPOND TO WHAT THE CUSTOMER ACTUALLY WROTE — this is the #1 rule.\n"
        "Every reply must directly address the customer's latest message. Never ignore it,\n"
        "never answer a different question, never restart the conversation. If their message\n"
        "is ambiguous, ask ONE short clarifying question that references their exact words\n"
        "(e.g. customer: 'מד מים' → 'כשאתה אומר מד מים — הכוונה לחיישן חום מים או למד לחץ?').\n"
        "If the customer corrects you ('לא ביקשתי X', 'זה לא מה שאמרתי') — apologize in ONE\n"
        "short sentence, drop your previous assumption completely, and work only from their\n"
        "correction. If the customer is frustrated or insults, stay calm and warm, acknowledge\n"
        "briefly, and immediately move the conversation forward on THEIR terms.\n"
        "If the customer already told you something (part, side, vehicle) — NEVER ask for it\n"
        "again; read the conversation history before asking anything.\n"
        "Sound like a skilled Israeli parts guy on WhatsApp: short, warm, direct, no corporate\n"
        "filler, at most one emoji.\n"
        "\n"
        "If the next step is collecting details, ask one clear question and give one compact example.\n"
        "If search results are provided, use only those values and do not invent numbers.\n"
        "If no results, say what you searched in their words, then ask for one concrete refinement\n"
        "(OEM number, front/rear, or the exact system it belongs to).\n"
        "NEVER write a payment link/URL yourself — payment links are attached by the system only.\n"
        "Avoid generic openers like 'I am here to help' unless the user explicitly asks for support availability.\n\n"
        f"[FLOW_INTENT]\n{flow_intent}\n\n"
        f"[FLOW_STATE_JSON]\n{json.dumps(flow_state, ensure_ascii=False)}\n"
    )

    # Keep a short memory window so wording remains contextual but stable.
    messages = history[-6:] + [{"role": "user", "content": user_message or "continue"}]

    try:
        reply = await agent.think(messages, system_override=system, source=source)
        return reply, model_used
    except Exception as e:
        print(f"[PartsFlow] inferred reply failed ({agent_name}): {e}")
        return agent._offline_reply(messages), model_used


async def _format_response_for_customer(
    raw_response: str,
    agent_name: str,
    source: str,
    history: List[Dict[str, str]],
) -> str:
    """Use Gemini to reformat raw agent response into warm customer-facing text. Skips short replies."""
    fast_agents = {"router_agent", "orders_agent", "security_agent", "tech_agent",
                   "supplier_manager_agent", "social_media_manager_agent", "parts_finder_agent"}
    if agent_name not in fast_agents:
        return raw_response
    # Skip reformatting for short replies — they are already good
    if len(raw_response) < 120:
        return raw_response

    last_user_msg = ""
    for m in reversed(history):
        if m.get("role") == "user":
            last_user_msg = m.get("content", "")
            break

    if _normalize_source(source) == "telegram":
        system = """You are the Telegram customer-service editor for Auto Spare Finder.
Rewrite the raw response naturally, while strictly preserving facts and links from backend data only.

Mandatory rules:
1. Same language as customer input only (Hebrew or Arabic).
2. No invented details.
3. No cart mentions.
4. No fake links; preserve real backend links exactly.
5. Keep it short, warm, and professional (max 4 sentences).
6. No HTML/markdown formatting.
7. No marketing text, no compatibility claims, no shipping promises unless explicitly in raw data."""
    else:
        system = """אתה עורך לשון של שירות לקוחות ישראלי.
קיבלת תשובה גולמית. עליך לנסח אותה מחדש — חמה, קצרה, מקצועית.

חוקי שפה מחייבים:
1. אם הלקוח כתב עברית — התשובה כולה בעברית. אסור אף מילה בערבית, סינית, או אנגלית. מונחים טכניים בלבד כמו OEM מותרים.
2. אם הלקוח כתב ערבית — התשובה כולה בערבית. אסור אף מילה בעברית.
3. אסור לערבב אותיות משפות שונות באותה מילה.
4. לא יותר מ-4 משפטים.
5. ללא HTML, markdown, או קישורים.
6. טון חם — כמו נציג שירות אנושי."""

    prompt = f"""תשובה גולמית מהמערכת:
{raw_response}

הודעת הלקוח:
{last_user_msg}

נסח מחדש בצורה טבעית וחמה:"""

    try:
        return await hf_text(prompt, system=system)
    except Exception:
        return raw_response


_WHATSAPP_ANON_USER_ID = "00000000-0000-0000-0000-000000000001"

_CHECKOUT_METRICS_SOURCES = ("whatsapp", "telegram", "web")
_checkout_metrics_lock = Lock()
_checkout_metrics: Dict[str, Dict[str, Any]] = {
    src: {
        "attempts": 0,
        "successes": 0,
        "failures": 0,
        "last_error": None,
        "updated_at": None,
    }
    for src in _CHECKOUT_METRICS_SOURCES
}


def _normalize_checkout_metric_source(source: Optional[str]) -> str:
    source_key = _normalize_source(source)
    if source_key not in _CHECKOUT_METRICS_SOURCES:
        return "web"
    return source_key


def _record_checkout_link_metric(source: Optional[str], success: bool, error_message: Optional[str] = None) -> None:
    source_key = _normalize_checkout_metric_source(source)
    with _checkout_metrics_lock:
        bucket = _checkout_metrics.setdefault(
            source_key,
            {"attempts": 0, "successes": 0, "failures": 0, "last_error": None, "updated_at": None},
        )
        bucket["attempts"] = int(bucket.get("attempts") or 0) + 1
        if success:
            bucket["successes"] = int(bucket.get("successes") or 0) + 1
            bucket["last_error"] = None
        else:
            bucket["failures"] = int(bucket.get("failures") or 0) + 1
            bucket["last_error"] = str(error_message or "unknown_error")[:240]
        bucket["updated_at"] = datetime.utcnow().isoformat()

        attempts = int(bucket.get("attempts") or 0)
        successes = int(bucket.get("successes") or 0)
        failures = int(bucket.get("failures") or 0)
        last_error = bucket.get("last_error")

    success_rate = (float(successes) / float(attempts) * 100.0) if attempts else 0.0
    if success:
        logger.info(
            "[CheckoutMetrics] source=%s status=success attempts=%d successes=%d failures=%d success_rate_pct=%.2f",
            source_key,
            attempts,
            successes,
            failures,
            success_rate,
        )
    else:
        logger.warning(
            "[CheckoutMetrics] source=%s status=failure attempts=%d successes=%d failures=%d success_rate_pct=%.2f error=%s",
            source_key,
            attempts,
            successes,
            failures,
            success_rate,
            last_error,
        )


def get_checkout_link_metrics_snapshot() -> Dict[str, Dict[str, Any]]:
    with _checkout_metrics_lock:
        snapshot: Dict[str, Dict[str, Any]] = {}
        for src, row in _checkout_metrics.items():
            attempts = int(row.get("attempts") or 0)
            successes = int(row.get("successes") or 0)
            failures = int(row.get("failures") or 0)
            snapshot[src] = {
                "attempts": attempts,
                "successes": successes,
                "failures": failures,
                "success_rate_pct": round((float(successes) / float(attempts) * 100.0), 2) if attempts else 0.0,
                "last_error": row.get("last_error"),
                "updated_at": row.get("updated_at"),
            }
    return snapshot


async def create_checkout_link(
    part_id: str,
    quantity: int,
    user_id: str,
    shipping_address: dict,
    source: str = "whatsapp",
) -> str:
    """
    Generate a Stripe checkout URL for chatbot channels without JWT auth.
    Returns the checkout URL string, or an error string starting with "ERROR:".
    Callable for registered and anonymous guest users.
    """
    # Allow anonymous users — treat them as guest, use anon user_id directly
    if str(user_id) == _WHATSAPP_ANON_USER_ID:
        pass
    from routes.payments import create_whatsapp_checkout
    result = await create_whatsapp_checkout(
        user_id=user_id,
        part_id=part_id,
        quantity=quantity,
        shipping_address=shipping_address,
        source=source,
    )
    if result.get("ok"):
        # Hand customers a short branded link (autosparefinder.co.il/pay/XXX)
        # instead of the ~120-char Stripe URL (added 2026-07-05).
        try:
            from routes.payments import shorten_payment_url
            return await shorten_payment_url(result["checkout_url"])
        except Exception:
            return result["checkout_url"]
    return f"ERROR: {result.get('error', 'Unknown error')}"



async def process_user_message(
    user_id: str,
    message: str,
    conversation_id: Optional[str],
    db: AsyncSession,
    source: str = "web",
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

    shared_memory_rows = await _load_shared_memory(
        db=db,
        user_id=str(user_id),
        conversation_id=str(conversation.id),
        agent_name=None,
    )
    shared_memory_prompt = _render_shared_memory_prompt(shared_memory_rows)
    history_for_agents = _inject_shared_memory_context(history, shared_memory_prompt)

    # Context state for deterministic parts intake flow.
    context_data = dict(conversation.context or {})
    known_plate = str(context_data.get("license_plate") or "").strip()
    had_plate_before = bool(known_plate)
    intro_sent = bool(context_data.get("intro_sent"))
    incoming_plate = _extract_license_plate(message)
    parts_flow_active = bool(context_data.get("parts_flow_active"))
    vehicle_profile = context_data.get("vehicle_profile") if isinstance(context_data.get("vehicle_profile"), dict) else None
    vehicle_confirmed = bool(context_data.get("vehicle_confirmed"))
    last_part_query = str(context_data.get("last_part_query") or "").strip()
    try:
        last_results_count = int(context_data.get("last_results_count") or 0)
    except Exception:
        last_results_count = 0
    try:
        vehicle_confirm_retries = int(context_data.get("vehicle_confirm_retries") or 0)
    except Exception:
        vehicle_confirm_retries = 0
    try:
        part_prompt_retries = int(context_data.get("part_prompt_retries") or 0)
    except Exception:
        part_prompt_retries = 0
    pre_route_result: Optional[Dict[str, Any]] = None
    agent_error: Optional[str] = None

    if incoming_plate and incoming_plate != known_plate:
        context_data["license_plate"] = incoming_plate
        context_data["vehicle_confirmed"] = False
        context_data.pop("vehicle_profile", None)
        known_plate = incoming_plate
        vehicle_profile = None
        vehicle_confirmed = False

    # Vehicle change by NAME (added 2026-07-05): a stored vehicle_profile used
    # to persist forever — a customer saying "פילטר שמן למאזדה 3" while the
    # profile still held an old Citroen kept getting replies about the Citroen.
    # If the message names a manufacturer that differs from the stored profile,
    # drop the stale vehicle so the flow re-identifies (asks for the plate and
    # pulls full specs from the gov API).
    if vehicle_profile and not incoming_plate:
        _stored_make = str(
            vehicle_profile.get("manufacturer") or vehicle_profile.get("make") or ""
        ).strip().lower()
        _MAKE_ALIASES = {
            "toyota": ["toyota", "טויוטה"], "mazda": ["mazda", "מאזדה", "מזדה"],
            "hyundai": ["hyundai", "יונדאי"], "kia": ["kia", "קיה"],
            "citroen": ["citroen", "סיטרואן"], "peugeot": ["peugeot", "פיגו", "פג'ו"],
            "renault": ["renault", "רנו"], "volkswagen": ["volkswagen", "vw", "פולקסווגן"],
            "skoda": ["skoda", "סקודה"], "seat": ["seat", "סיאט"],
            "audi": ["audi", "אאודי", "אודי"], "bmw": ["bmw", "ב.מ.וו", 'ב"מ'],
            "mercedes": ["mercedes", "מרצדס"], "ford": ["ford", "פורד"],
            "honda": ["honda", "הונדה"], "nissan": ["nissan", "ניסאן", "ניסן"],
            "suzuki": ["suzuki", "סוזוקי"], "mitsubishi": ["mitsubishi", "מיצובישי"],
            "subaru": ["subaru", "סובארו"], "chevrolet": ["chevrolet", "שברולט"],
            "opel": ["opel", "אופל"], "fiat": ["fiat", "פיאט"],
            "volvo": ["volvo", "וולוו"], "lexus": ["lexus", "לקסוס"],
        }
        _msg_l = f" {(message or '').lower()} "
        for _canon, _aliases in _MAKE_ALIASES.items():
            if any(f" {a} " in _msg_l or _msg_l.strip().startswith(a + " ") for a in _aliases):
                if _canon not in _stored_make and _stored_make not in _canon:
                    print(f"[PartsFlow] vehicle changed by name: '{_stored_make}' → '{_canon}' — resetting vehicle context")
                    context_data.pop("vehicle_profile", None)
                    context_data["vehicle_confirmed"] = False
                    context_data.pop("license_plate", None)
                    context_data["part_prompt_retries"] = 0
                    vehicle_profile = None
                    vehicle_confirmed = False
                    known_plate = ""
                    part_prompt_retries = 0
                break

    if incoming_plate or _has_part_signal(message):
        parts_flow_active = True
    elif not parts_flow_active:
        # Telegram and other channels can still use full router behavior.
        # Only enable the strict plate->gov->part flow when intent is parts-related.
        try:
            router = get_agent("router_agent")
            pre_route_result = await router.route(
                message,
                {
                      "history_length": len(history),
                      "source": source,
                      "route_stage": "precheck",
                      "shared_memory_prompt": shared_memory_prompt,
                  },
            )
            pre_agent = pre_route_result.get("agent", "service_agent")
            if pre_agent in ("parts_finder_agent", "sales_agent"):
                parts_flow_active = True
        except Exception as e:
            print(f"[PartsFlow] pre-route failed, continuing without parts flow: {e}")

    # If parts flow is already confirmed but user now asks a non-parts topic,
    # let router hand off to system agents (security/orders/finance/etc.).
    if (
        parts_flow_active
        and vehicle_confirmed
        and not incoming_plate
        and not _has_part_signal(message)
        and _should_router_exit_parts_flow(message)
    ):
        try:
            if pre_route_result is None:
                router = get_agent("router_agent")
                pre_route_result = await router.route(
                    message,
                    {
                        "history_length": len(history),
                        "source": source,
                        "route_stage": "parts_exit_check",
                        "shared_memory_prompt": shared_memory_prompt,
                    },
                )
            pre_agent = pre_route_result.get("agent", "service_agent")
            if pre_agent not in ("parts_finder_agent", "sales_agent", "service_agent"):
                parts_flow_active = False
                context_data["parts_flow_active"] = False
        except Exception as e:
            print(f"[PartsFlow] exit-check failed, keeping parts flow active: {e}")
    context_data["parts_flow_active"] = parts_flow_active

    # ── 3. Save user message ───────────────────────────────────────────────────
    user_msg = Message(
        conversation_id=conversation.id,
        role="user",
        content=message,
        content_type="text",
    )
    db.add(user_msg)
    await db.flush()

    db.add(AgentAction(
        message_id=user_msg.id,
        agent_name="router_agent",
        action_type="inbound_message",
        action_data={
            "source": source,
            "conversation_id": str(conversation.id),
            "parts_flow_active": bool(parts_flow_active),
            "incoming_plate": bool(incoming_plate),
            "message_preview": _compact_preview(message),
            "message_length": len(str(message or "")),
        },
        success=True,
        execution_time_ms=0,
    ))

    plate_just_captured = bool(known_plate) and not had_plate_before
    quick_part_choice = _quick_part_from_message(message)
    effective_message = quick_part_choice or message

    _msg_lang = ""
    if any("\u0600" <= ch <= "\u06FF" for ch in (message or "")):
        _msg_lang = "ar"
    elif any("\u0590" <= ch <= "\u05FF" for ch in (message or "")):
        _msg_lang = "he"
    elif any(ch.isalpha() for ch in (message or "")):
        _msg_lang = "en"
    if _msg_lang:
        context_data["preferred_lang"] = _msg_lang
    _lang = str(context_data.get("preferred_lang") or _msg_lang or "he")

    if parts_flow_active:
        # ── Checkout intent: user replies 1/2/3 after seeing WhatsApp/Telegram results ──
        _pending_checkout = context_data.get("pending_checkout_parts") or []
        _checkout_choice = None
        _checkout_msg = (message or "").strip()
        if _pending_checkout and source in ("whatsapp", "telegram", "web") and vehicle_confirmed:
            if _checkout_msg in ("1", "2", "3"):
                _checkout_choice = int(_checkout_msg)
            # Accept plain confirmation too (added 2026-07-05): customers answer
            # "כן"/"yes" instead of a number — that used to fall through to the
            # LLM, which hallucinated a fake Stripe URL. A bare confirmation
            # selects the first (cheapest) offer.
            elif _is_confirm_yes(_checkout_msg):
                _checkout_choice = 1
            # Explicit payment-link request ("תן לינק לתשלום", "שלח קישור") while
            # offers are pending — same intent as confirming the first offer.
            elif re.search(r"לינק|קישור|link|תשלום|לשלם|pay", _checkout_msg, re.IGNORECASE):
                _checkout_choice = 1
            # Free-text order intent ("אני רוצה להזמין", "I want to order") with no
            # NEW part named → the customer wants to buy the part we just showed.
            # Selects the first (best-ranked) offer instead of dropping to the
            # generic orders agent / cart redirect (gap fixed 2026-07-09).
            elif _is_order_intent(_checkout_msg) and not _has_part_signal(_checkout_msg):
                _checkout_choice = 1

        if _checkout_choice is not None:
            _chosen = next(
                (p for p in _pending_checkout if p["idx"] == _checkout_choice), None
            )

            if _chosen and _chosen.get("part_id"):
                # Load shipping address from user profile
                _ship_addr: dict = {"city": "ישראל", "address_line1": "לא צוינה"}
                if str(user_id) != _WHATSAPP_ANON_USER_ID:
                    try:
                        from BACKEND_DATABASE_MODELS import pii_session_factory as _pii_sf2, UserProfile
                        async with _pii_sf2() as _pdb:
                            import uuid as _uuid2
                            _prof_res = await _pdb.execute(
                                select(UserProfile).where(UserProfile.user_id == _uuid2.UUID(str(user_id)))
                            )
                            _prof = _prof_res.scalar_one_or_none()
                            if _prof and _prof.city:
                                _ship_addr = {
                                    "address_line1": _prof.address_line1 or "",
                                    "city": _prof.city or "ישראל",
                                    "postal_code": _prof.postal_code or "",
                                }
                    except Exception:
                        pass

                start_time = datetime.utcnow()
                _checkout_url = await create_checkout_link(
                    part_id=_chosen["part_id"],
                    quantity=1,
                    user_id=str(user_id),
                    shipping_address=_ship_addr,
                    source=source,
                )
                _checkout_success = not _checkout_url.startswith("ERROR:")
                _record_checkout_link_metric(
                    source=source,
                    success=_checkout_success,
                    error_message=None if _checkout_success else _checkout_url,
                )

                if _lang == "ar":
                    _ok_prefix = "ممتاز! إليك رابط الدفع الآمن: "
                    _register_msg = "للطلب عبر واتساب، سجّل أولاً في: autosparefinder.co.il"
                    _error_msg = "تعذر إنشاء رابط الدفع الآن. حاول مرة أخرى خلال دقيقة."
                elif _lang == "en":
                    _ok_prefix = "Great! Here's your secure payment link: "
                    _register_msg = "To order via WhatsApp, please register first at: autosparefinder.co.il"
                    _error_msg = "I couldn't create a payment link right now. Please try again in a minute."
                else:
                    _ok_prefix = "מעולה! הנה קישור התשלום המאובטח שלך: "
                    _register_msg = "להזמנה דרך וואטסאפ, הירשם תחילה ב: autosparefinder.co.il"
                    _error_msg = "לא הצלחתי ליצור קישור תשלום כרגע. נסה שוב בעוד דקה."

                if _checkout_url.startswith("ERROR:"):
                    _err_l = _checkout_url.lower()
                    if "not registered" in _err_l and source in ("whatsapp", "telegram"):
                        response_text = _register_msg
                    else:
                        response_text = _error_msg
                else:
                    context_data.pop("pending_checkout_parts", None)
                    response_text = _ok_prefix + _checkout_url

                route_result = {
                    "agent": "parts_finder_agent",
                    "confidence": 1.0,
                    "language": "he",
                    "intent": f"{source}_checkout",
                    "extracted_data": {"part_id": _chosen["part_id"]},
                }
                agent_name = "parts_finder_agent"
                model_used = _channel_model_for_source(source, FREE_MODEL)
                exec_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            else:
                # No valid part in context — fall through to normal flow
                _checkout_choice = None

        # VIN capture (added 2026-07-13): a customer who sends their VIN — typed, or
        # read by vision from a windshield/chassis-plate photo — should have the car
        # decoded automatically, not be asked to type make/model/year. We decode via
        # NHTSA (cached + circuit-broken) and drop the result into context_data, which
        # the free-text block just below then treats exactly like a customer-stated car.
        # NHTSA reliably returns make+year even for EU VINs (model can come back blank
        # for European models — then make+year still seeds the flow and we ask only the
        # model, instead of the whole car).
        if (
            _checkout_choice is None
            and not known_plate
            and not (vehicle_confirmed and isinstance(vehicle_profile, dict))
        ):
            _vin = _extract_vin_from_text(message)
            if _vin and not context_data.get("vin"):
                try:
                    from routes.parts import _decode_vin_with_resilience
                    _vd = await _decode_vin_with_resilience(_vin)
                except Exception as _ve:
                    _vd = None
                    print(f"[PartsFlow] VIN decode failed for {_vin}: {str(_ve)[:80]}")
                if _vd and str(_vd.get("manufacturer") or "").strip():
                    _vmake = str(_vd.get("manufacturer") or "").strip()
                    _vmodel = str(_vd.get("model") or "").strip()
                    _vyear = str(_vd.get("year") or "").strip()
                    context_data["vin"] = _vin
                    if _vmake:
                        context_data["vehicle_manufacturer"] = _vmake
                    if _vmodel:
                        context_data["vehicle_model"] = _vmodel
                    if _vyear and _vyear != "0":
                        context_data["vehicle_year"] = _vyear
                    print(f"[PartsFlow] VIN decoded: {_vin} -> {_vmake}/{_vmodel}/{_vyear}")

        # Free-text vehicle capture (added 2026-07-09): when there is no plate yet
        # but the customer names make (+ model) + year in free text, build a
        # CONFIRMED vehicle profile up front so the SAME fitment-first search path
        # runs — the customer should not be forced to send a plate when they've
        # already told us the car. Deterministic (LLM-independent) so it still
        # works when the chat models are 429-throttled.
        if (
            _checkout_choice is None
            and not known_plate
            and not (vehicle_confirmed and isinstance(vehicle_profile, dict))
        ):
            _ft_make, _ft_model, _ft_year = _extract_vehicle_from_text(message)
            _ft_make = _ft_make or str(context_data.get("vehicle_manufacturer") or "").strip()
            _ft_model = _ft_model or str(context_data.get("vehicle_model") or "").strip()
            _ft_year = _ft_year or str(context_data.get("vehicle_year") or "").strip()
            if _ft_make:
                context_data["vehicle_manufacturer"] = _ft_make
            if _ft_model:
                context_data["vehicle_model"] = _ft_model
            if _ft_year:
                context_data["vehicle_year"] = _ft_year
            if (_ft_make or _ft_model) and _ft_year:
                vehicle_profile = {
                    "manufacturer": _ft_make,
                    "model": _ft_model,
                    "year": _ft_year,
                    "engine_type": "",
                    "license_plate": None,
                    "source": "customer_provided",
                }
                vehicle_confirmed = True
                context_data["vehicle_profile"] = vehicle_profile
                context_data["vehicle_confirmed"] = True
                print(f"[PartsFlow] free-text vehicle captured: {_ft_make}/{_ft_model}/{_ft_year}")

        # ── Step 1: intro + ask for plate ────────────────────────────────────────
        if _checkout_choice is not None:
            pass  # checkout URL already built above, skip normal flow
        elif not known_plate and not (vehicle_confirmed and isinstance(vehicle_profile, dict)):
            # No plate and no confirmed free-text vehicle → ask for a plate or
            # make + model + year. (Free-text make+model+year is captured by the
            # pre-capture block above, which builds a confirmed profile and routes
            # straight to the search branch below.)
            start_time = datetime.utcnow()
            if not intro_sent:
                context_data["intro_sent"] = True
            response_text, model_used = await _infer_parts_flow_reply(
                agent_name="service_agent",
                source=source,
                history=history,
                user_message=message,
                flow_intent="collect_license_plate_or_vehicle_info",
                flow_state={
                    "intro_sent": bool(context_data.get("intro_sent")),
                    "known_plate": known_plate or None,
                    "supported_plate_formats": ["12-345-67", "123-45-678", "1234567", "12345678"],
                    "alternative": "or provide manufacturer + model + year",
                },

                shared_memory_prompt=shared_memory_prompt,)
            route_result = {
                "agent": "service_agent",
                "confidence": 1.0,
                "language": "he",
                "intent": "collect_license_plate_or_vehicle_info",
                "extracted_data": {},
            }
            agent_name = "service_agent"
            exec_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        else:
            # Step 2: resolve vehicle details from gov.il and ask for confirmation
            if not vehicle_profile or str(vehicle_profile.get("license_plate") or "") != known_plate:
                start_time = datetime.utcnow()
                context_data["part_prompt_retries"] = 0
                pf = get_agent("parts_finder_agent")
                try:
                    vehicle_profile = await pf.identify_vehicle(known_plate, db)
                    context_data["vehicle_profile"] = vehicle_profile
                    context_data["vehicle_confirmed"] = False
                    vehicle_confirmed = False
                    response_text, model_used = await _infer_parts_flow_reply(
                        agent_name="parts_finder_agent",
                        source=source,
                        history=history,
                        user_message=message,
                        flow_intent="vehicle_details_confirmation",
                        flow_state={
                            "license_plate": known_plate,
                            "vehicle": {
                                "manufacturer": vehicle_profile.get("manufacturer"),
                                "model": vehicle_profile.get("model"),
                                "year": vehicle_profile.get("year"),
                                "engine_type": vehicle_profile.get("engine_type"),
                                "fuel_type": vehicle_profile.get("fuel_type"),
                            },
                            "requires_yes_no_confirmation": True,
                        },
                    
                        shared_memory_prompt=shared_memory_prompt,)
                    route_result = {
                        "agent": "parts_finder_agent",
                        "confidence": 1.0,
                        "language": "he",
                        "intent": "vehicle_details_confirmation",
                        "extracted_data": {
                            "license_plate": known_plate,
                            "vehicle": {
                                "manufacturer": vehicle_profile.get("manufacturer"),
                                "model": vehicle_profile.get("model"),
                                "year": vehicle_profile.get("year"),
                            },
                        },
                    }
                except Exception as e:
                    print(f"[PartsFlow] identify_vehicle failed for {known_plate}: {e}")
                    context_data["vehicle_confirmed"] = False
                    context_data.pop("vehicle_profile", None)
                    vehicle_profile = None
                    vehicle_confirmed = False
                    response_text, model_used = await _infer_parts_flow_reply(
                        agent_name="service_agent",
                        source=source,
                        history=history,
                        user_message=message,
                        flow_intent="vehicle_lookup_failed",
                        flow_state={
                            "license_plate": known_plate,
                            "error": str(e),
                            "next_required_step": "ask_for_new_or_correct_plate",
                        },
                    
                        shared_memory_prompt=shared_memory_prompt,)
                    route_result = {
                        "agent": "service_agent",
                        "confidence": 0.9,
                        "language": "he",
                        "intent": "vehicle_lookup_failed",
                        "extracted_data": {"license_plate": known_plate},
                    }
                    agent_name = "service_agent"
                else:
                    agent_name = "parts_finder_agent"
                exec_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            # Step 3: user confirms vehicle details
            elif not vehicle_confirmed and not _has_part_signal(effective_message):
                start_time = datetime.utcnow()
                if _is_confirm_yes(message):
                    context_data["vehicle_confirmed"] = True
                    context_data["vehicle_confirm_retries"] = 0
                    response_text, model_used = await _infer_parts_flow_reply(
                        agent_name="parts_finder_agent",
                        source=source,
                        history=history,
                        user_message=message,
                        flow_intent="vehicle_confirmed_ask_part",
                        flow_state={
                            "license_plate": known_plate,
                            "vehicle": vehicle_profile,
                            "vehicle_confirmed": True,
                            "next_required_step": "ask_for_part_name",
                        },
                    
                        shared_memory_prompt=shared_memory_prompt,)
                    route_result = {
                        "agent": "parts_finder_agent",
                        "confidence": 1.0,
                        "language": "he",
                        "intent": "vehicle_confirmed_ask_part",
                        "extracted_data": {"license_plate": known_plate},
                    }
                    agent_name = "parts_finder_agent"
                elif _is_confirm_no(message):
                    context_data.pop("license_plate", None)
                    context_data.pop("vehicle_profile", None)
                    context_data["vehicle_confirmed"] = False
                    known_plate = ""
                    response_text, model_used = await _infer_parts_flow_reply(
                        agent_name="service_agent",
                        source=source,
                        history=history,
                        user_message=message,
                        flow_intent="vehicle_rejected_request_new_plate",
                        flow_state={
                            "vehicle_confirmed": False,
                            "next_required_step": "ask_for_new_plate",
                            "supported_plate_formats": ["12-345-67", "123-45-678", "1234567", "12345678"],
                        },
                    
                        shared_memory_prompt=shared_memory_prompt,)
                    route_result = {
                        "agent": "service_agent",
                        "confidence": 1.0,
                        "language": "he",
                        "intent": "vehicle_rejected_request_new_plate",
                        "extracted_data": {},
                    }
                    agent_name = "service_agent"
                else:
                    vehicle_confirm_retries += 1
                    context_data["vehicle_confirm_retries"] = vehicle_confirm_retries
                    if vehicle_confirm_retries >= 2:
                        # Stop confirmation loops: proceed to concrete part prompt.
                        context_data["vehicle_confirmed"] = True
                        response_text, model_used = await _infer_parts_flow_reply(
                            agent_name="parts_finder_agent",
                            source=source,
                            history=history,
                            user_message=message,
                            flow_intent="ask_part_after_vehicle_confirmation",
                            flow_state={
                                "license_plate": known_plate,
                                "vehicle": vehicle_profile,
                                "last_part_query": last_part_query or None,
                                "last_results_count": last_results_count,
                                "quick_part_choices": _QUICK_PART_CHOICES,
                                "followup_mode": "request_exact_part_or_oem",
                            },
                            shared_memory_prompt=shared_memory_prompt,
                        )
                        agent_name = "parts_finder_agent"
                    else:
                        response_text, model_used = await _infer_parts_flow_reply(
                            agent_name="service_agent",
                            source=source,
                            history=history,
                            user_message=message,
                            flow_intent="await_vehicle_confirmation",
                            flow_state={
                                "license_plate": known_plate,
                                "vehicle": vehicle_profile,
                                "vehicle_summary": _vehicle_summary_he(vehicle_profile or {}),
                                "requires_yes_no_confirmation": True,
                            },
                        
                            shared_memory_prompt=shared_memory_prompt,)
                        agent_name = "service_agent"
                    route_result = {
                        "agent": agent_name,
                        "confidence": 0.95,
                        "language": "he",
                        "intent": "await_vehicle_confirmation",
                        "extracted_data": {"license_plate": known_plate},
                    }
                exec_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            # Step 4: confirmed vehicle -> part search + price answer
            # Gate rewritten 2026-07-05: _has_part_signal is a keyword list and
            # inevitably misses real part names ("מד מים", "צינור", "מד לחץ") —
            # customers who named their part were told "which part do you
            # want?" over and over. New rule: once the vehicle is confirmed,
            # ONLY noise/greetings/bare confirmations go to the ask-for-part
            # branch; every substantive message is treated as a part query —
            # search it, and if nothing matches the model asks a clarifying
            # question grounded in what the customer actually wrote.
            elif not _has_part_signal(effective_message) and (
                _is_smalltalk_or_noise(effective_message) or _is_confirm_yes(message)
            ):
                start_time = datetime.utcnow()
                part_prompt_retries += 1
                context_data["part_prompt_retries"] = part_prompt_retries
                handled_no_part_prompt = False
                followup_mode = "request_exact_part_or_oem"
                if (message or "").strip() == "4":
                    followup_mode = "await_free_text_part_name"
                    handled_no_part_prompt = True
                elif _is_confirm_yes(message) and not last_part_query:
                    followup_mode = "show_quick_choices"
                    handled_no_part_prompt = True

                if not handled_no_part_prompt and _is_smalltalk_or_noise(message) and last_part_query:
                    if last_results_count > 0:
                        followup_mode = "offer_repeat_or_new_part_after_success"
                    else:
                        followup_mode = "request_refinement_after_no_results"
                elif not handled_no_part_prompt and not _is_confirm_yes(message) and _is_smalltalk_or_noise(message):
                    followup_mode = "ask_part_name_after_smalltalk"
                elif not handled_no_part_prompt and not _is_confirm_yes(message):
                    followup_mode = "request_exact_part_or_oem"

                if part_prompt_retries >= 3:
                    preferred_lang = str(context_data.get("preferred_lang") or "").strip().lower() or None
                    response_text = _human_recovery_reply(
                        message,
                        preferred_lang=preferred_lang,
                        vehicle_summary=_vehicle_summary_he(vehicle_profile or {}),
                        force_part_prompt=True,
                    )
                    model_used = _channel_model_for_source(source, FREE_MODEL)
                    # Reset after firing ONCE — this counter used to stay >=3
                    # forever, locking every subsequent message into the same
                    # canned template and never calling the AI again
                    # (root cause of the "dumb repeating bot", 2026-07-05).
                    part_prompt_retries = 0
                    context_data["part_prompt_retries"] = 0
                else:
                    response_text, model_used = await _infer_parts_flow_reply(
                        agent_name="parts_finder_agent",
                        source=source,
                        history=history,
                        user_message=message,
                        flow_intent="ask_part_after_vehicle_confirmation",
                        flow_state={
                            "license_plate": known_plate,
                            "vehicle": vehicle_profile,
                            "last_part_query": last_part_query or None,
                            "last_results_count": last_results_count,
                            "quick_part_choices": _QUICK_PART_CHOICES,
                            "followup_mode": followup_mode,
                        },
                    
                        shared_memory_prompt=shared_memory_prompt,)
                route_result = {
                    "agent": "parts_finder_agent",
                    "confidence": 0.95,
                    "language": "he",
                    "intent": "ask_part_after_vehicle_confirmation",
                    "extracted_data": {"license_plate": known_plate},
                }
                agent_name = "parts_finder_agent"
                exec_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            else:
                pf = get_agent("parts_finder_agent")
                search_q = pf._extract_search_query(effective_message)
                if incoming_plate:
                    search_q = search_q.replace(incoming_plate, "").strip(" -:")
                if len(search_q.strip()) < 2:
                    search_q = effective_message.strip()
                # Strip the customer's restated car from the part query (added
                # 2026-07-09): "מסנן שמן לטויוטה קורולה 2018" → "מסנן שמן". The
                # vehicle words otherwise poison the fitment-filtered search and
                # return 0 (part names never contain "corolla 2018").
                _mk_hint = (vehicle_profile or {}).get("manufacturer") or ""
                _md_hint = (vehicle_profile or {}).get("model") or ""
                if _mk_hint or _md_hint:
                    _cleaned_q = _strip_vehicle_terms(search_q, _mk_hint, _md_hint)
                    if len(_cleaned_q) >= 2:
                        search_q = _cleaned_q
                # Price/quantity/how-much follow-up with no NEW part named
                # ("כמה זה עולה?", "how much?", "יש במלאי?") → the customer means
                # the part we were just discussing. Reuse the last part query so
                # we re-price it instead of searching for the follow-up phrase.
                if last_part_query and not _has_part_signal(effective_message) and not _quick_part_from_message(message):
                    search_q = last_part_query

                category_hint = pf._extract_category_hint(search_q)
                manufacturer_hint = (vehicle_profile or {}).get("manufacturer") or None
                start_time = datetime.utcnow()

                # Tier 0 (/goal 2026-07-05): FITMENT-VERIFIED search first —
                # only parts with a part_vehicle_fitment row matching the
                # customer's confirmed car (make+model+year from the gov API).
                # These are "the right parts for THIS car", not text matches.
                # make + category are KEPT (owner directive 2026-07-09): they
                # stop us selling the wrong part. When the category filter is
                # thin/empty, we do NOT silently show cross-category parts — we
                # fall back progressively and the agent verifies with the client.
                verified_fit = False
                results = await pf.search_parts_in_db(
                    query=search_q,
                    vehicle_id=None,
                    category=category_hint,
                    db=db,
                    limit=5,
                    sort_by="name",  # relevance order
                    vehicle_manufacturer=manufacturer_hint,
                    user_id=str(user_id),
                    vehicle_profile=vehicle_profile,
                )
                # Tier 0b: fitment + make WITHOUT category — the category
                # vocabulary is inconsistent (the same oil filter sits in
                # 'filters'/'engine'/'כללי'/'general'), so a category miss must
                # not discard a genuinely fitment-VERIFIED part. Still 100% safe:
                # the part_vehicle_fitment row proves it fits THIS exact car.
                if not results:
                    results = await pf.search_parts_in_db(
                        query=search_q,
                        vehicle_id=None,
                        category=None,
                        db=db,
                        limit=5,
                        sort_by="name",
                        vehicle_manufacturer=manufacturer_hint,
                        user_id=str(user_id),
                        vehicle_profile=vehicle_profile,
                    )
                if results:
                    verified_fit = True

                # Tier 1: same make + category, fitment not verified (coverage is
                # partial — a real part may simply lack a fitment row yet).
                if not results:
                    results = await pf.search_parts_in_db(
                        query=search_q,
                        vehicle_id=None,
                        category=category_hint,
                        db=db,
                        limit=5,
                        sort_by="name",  # relevance order (ranked.pos) — price_asc buried best matches under cheap junk,
                        vehicle_manufacturer=manufacturer_hint,
                        user_id=str(user_id),
                    )
                # Tier 1b: same make WITHOUT category (category vocab mismatch).
                if not results:
                    results = await pf.search_parts_in_db(
                        query=search_q,
                        vehicle_id=None,
                        category=None,
                        db=db,
                        limit=5,
                        sort_by="name",
                        vehicle_manufacturer=manufacturer_hint,
                        user_id=str(user_id),
                    )

                # Tier 2: RELAXED query, still make (+fitment). A multi-word Hebrew
                # query with a trailing qualifier over-constrains against English
                # catalog names — "רפידות בלם קדמיות" returns 0 but "רפידות בלם"
                # returns 5 ("Brake Pads Front"). Progressively drop trailing words
                # (keep make so we never cross-brand). Fitment-first, then make.
                if not results:
                    _tokens = [t for t in re.split(r"\s+", (search_q or "").strip()) if t]
                    for _n in range(len(_tokens) - 1, 1, -1):   # try first n-1 … 2 words
                        _rq = " ".join(_tokens[:_n])
                        results = await pf.search_parts_in_db(
                            query=_rq, vehicle_id=None, category=None, db=db, limit=5,
                            sort_by="name", vehicle_manufacturer=manufacturer_hint,
                            user_id=str(user_id), vehicle_profile=vehicle_profile,
                        )
                        if results:
                            verified_fit = True
                            break
                        results = await pf.search_parts_in_db(
                            query=_rq, vehicle_id=None, category=None, db=db, limit=5,
                            sort_by="name", vehicle_manufacturer=manufacturer_hint,
                            user_id=str(user_id),
                        )
                        if results:
                            break

                # Fallback 1: last resort — broaden by removing the manufacturer
                # filter. These are NOT same-make so they show with the unverified
                # banner ("ההתאמה טרם אומתה — שלח OEM לוודא"): the agent explicitly
                # asks the client to verify rather than implying a guaranteed fit.
                if not results and manufacturer_hint:
                    results = await pf.search_parts_in_db(
                        query=search_q,
                        vehicle_id=None,
                        category=category_hint,
                        db=db,
                        limit=5,
                        sort_by="name",  # relevance order (ranked.pos) — price_asc buried best matches under cheap junk,
                        vehicle_manufacturer=None,
                        user_id=str(user_id),
                    )

                # Fallback 2: category-only search for generic part terms.
                if not results and category_hint:
                    results = await pf.search_parts_in_db(
                        query="",
                        vehicle_id=None,
                        category=category_hint,
                        db=db,
                        limit=5,
                        sort_by="name",  # relevance order (ranked.pos) — price_asc buried best matches under cheap junk,
                        vehicle_manufacturer=None,
                        user_id=str(user_id),
                    )

                context_data["last_part_query"] = search_q
                context_data["last_results_count"] = len(results)

                if results:
                    top = results[:3]
                    _formatted_lines: List[str] = []
                    # Fit banner (/goal 2026-07-05): the customer must know
                    # whether these parts are VERIFIED to fit their car or are
                    # same-make matches pending OEM confirmation.
                    _vsum = _vehicle_summary_he(vehicle_profile or {}, _lang)
                    # Language-aware banner + labels (2026-07-18): the customer must read the
                    # results in THEIR language (he/ar/en), not a hardcoded Hebrew/English mix.
                    if verified_fit:
                        _banner = {
                            "he": f"✅ נמצאו חלקים *מאומתים* לרכב שלך ({_vsum}):",
                            "ar": f"✅ تم العثور على قطع *مؤكّدة* لسيارتك ({_vsum}):",
                            "en": f"✅ Found *verified* parts for your vehicle ({_vsum}):",
                        }
                    else:
                        _banner = {
                            "he": f"נמצאו חלקים תואמים ל‑{_vsum} — ההתאמה המדויקת טרם אומתה. אם יש לך מספר OEM, שלח אותו לוודא 100%:",
                            "ar": f"تم العثور على قطع متوافقة مع {_vsum} — لم تُؤكَّد المطابقة الدقيقة بعد. إذا كان لديك رقم OEM فأرسله للتأكيد 100%:",
                            "en": f"Found parts matching {_vsum} — exact fit not verified yet. If you have an OEM number, send it to confirm 100%:",
                        }
                    _formatted_lines.append(_banner.get(_lang, _banner["he"]))
                    _formatted_lines.append("")
                    # per-language labels for the price / shipping / warranty lines
                    _vat_label = {"he": 'כולל מע"מ', "ar": "شامل الضريبة", "en": "incl. VAT"}.get(_lang, "incl. VAT")
                    _ship_word = {"he": "ימים", "ar": "أيام", "en": "days"}.get(_lang, "days")
                    _warr_tpl = {"he": "אחריות {m} חודשים", "ar": "ضمان {m} شهرًا", "en": "{m} months warranty"}.get(_lang, "{m} months warranty")
                    _pending_parts_payload: List[Dict[str, Any]] = []
                    for i, item in enumerate(top, start=1):
                        pr = item.get("pricing") or {}
                        _name = str(item.get("name") or "Part")
                        _manufacturer = str(item.get("manufacturer") or "Unknown")
                        _price_total = float(pr.get("total") or 0)
                        _delivery_days = int(pr.get("estimated_delivery_days") or 14)
                        _delivery_min = max(1, _delivery_days - 2)
                        _warranty_months = int(item.get("warranty_months") or 12)
                        _fit_mark = " ✅" if verified_fit else ""
                        _formatted_lines.extend(
                            [
                                f"*[{i}]. {_name}*{_fit_mark} — {_manufacturer}",
                                f"💰 ₪{_price_total:,.0f} ({_vat_label})",
                                f"🚚 {_delivery_min}–{_delivery_days} {_ship_word} | 🛡️ {_warr_tpl.format(m=_warranty_months)}",
                                "",
                            ]
                        )
                        _pending_parts_payload.append(
                            {
                                "idx": i,
                                "part_id": str(item.get("id") or ""),
                                "supplier_part_id": str(item.get("supplier_part_id") or ""),
                            }
                        )

                    context_data["pending_checkout_parts"] = _pending_parts_payload

                    if _lang == "ar":
                        _cta = "أي قطعة تريد طلبها؟ أرسل 1 أو 2 أو 3."
                    elif _lang == "en":
                        _cta = "Which part would you like to order? Reply 1, 2, or 3."
                    else:
                        _cta = "איזה חלק תרצה להזמין? שלח 1, 2 או 3."

                    response_text = "\n".join(_formatted_lines + [_cta]).strip()
                    model_used = _channel_model_for_source(source, FREE_MODEL)
                else:
                    context_data.pop("pending_checkout_parts", None)
                    response_text, model_used = await _infer_parts_flow_reply(
                        agent_name="parts_finder_agent",
                        source=source,
                        history=history,
                        user_message=message,
                        flow_intent="parts_price_search_no_results",
                        flow_state={
                            "license_plate": known_plate,
                            "vehicle": vehicle_profile,
                            "vehicle_summary": _vehicle_summary_he(vehicle_profile or {}),
                            "query": search_q,
                            "results_count": 0,
                            "next_required_step": "request_refinement_oem_or_front_rear_or_manufacturer",
                        },
                    
                        shared_memory_prompt=shared_memory_prompt,)

                route_result = {
                    "agent": "parts_finder_agent",
                    "confidence": 1.0,
                    "language": "he",
                    "intent": "parts_price_search",
                    "extracted_data": {
                        "license_plate": known_plate,
                        "query": search_q,
                        "results_count": len(results),
                    },
                }
                agent_name = "parts_finder_agent"
                exec_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
    else:
        # Non-parts conversation: use router + agent processing path.
        if pre_route_result is not None:
            route_result = pre_route_result
        else:
            router = get_agent("router_agent")
            route_result = await router.route(message, {"history_length": len(history), "source": source, "shared_memory_prompt": shared_memory_prompt})
        agent_name = route_result.get("agent", "service_agent")

        agent = get_agent(agent_name)
        model_used = _channel_model_for_source(source, agent.model)
        start_time = datetime.utcnow()
        try:
            response_text = await agent.process(
                message,
                history_for_agents,
                db,
                user_id=str(user_id),
                source=source,
                conversation_id=str(conversation.id),
                shared_memory_prompt=shared_memory_prompt,
            )
        except Exception as e:
            print(f"[ERROR] Agent {agent_name} failed: {e}")
            agent_error = str(e)
            response_text = "מצטער, נתקלתי בבעיה. אנא נסה שוב בעוד רגע."
            agent_name = "service_agent"
            model_used = _channel_model_for_source(source, FREE_MODEL)
        exec_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

    # Root anti-loop guard: prevent repeated or semantically-redundant assistant prompts.
    try:
        last_assistant_text = ""
        for _h in reversed(history):
            if _h.get("role") == "assistant":
                last_assistant_text = str(_h.get("content") or "").strip()
                break

        norm_current = re.sub(r"\s+", " ", str(response_text or "").strip())
        norm_prev = re.sub(r"\s+", " ", last_assistant_text)
        sem_current = _semantic_text_key(norm_current)
        sem_prev = _semantic_text_key(norm_prev)
        sem_same = bool(sem_current and sem_prev and (
            sem_current == sem_prev
            or (len(sem_current) > 28 and sem_current in sem_prev)
            or (len(sem_prev) > 28 and sem_prev in sem_current)
        ))
        reconfirm_loop = (
            _looks_vehicle_reconfirm_prompt(norm_current)
            and _looks_vehicle_reconfirm_prompt(norm_prev)
            and bool(context_data.get("vehicle_confirmed"))
        )

        if sem_same or reconfirm_loop:
            preferred_lang = str(context_data.get("preferred_lang") or "").strip().lower() or None
            if parts_flow_active and bool(context_data.get("vehicle_confirmed")):
                vtxt = _vehicle_summary_he(context_data.get("vehicle_profile") or {})
                response_text = _human_recovery_reply(
                    message,
                    preferred_lang=preferred_lang,
                    vehicle_summary=vtxt,
                    force_part_prompt=True,
                )
                agent_name = "parts_finder_agent"
                # Loop breaker (2026-07-05): if this exact recovery template was
                # ALREADY the previous reply, sending it again just continues the
                # loop the customer is complaining about. Reset the stale vehicle
                # and ask for the plate — the gov API fills in everything else.
                if _semantic_text_key(response_text) == norm_prev:
                    context_data.pop("vehicle_profile", None)
                    context_data["vehicle_confirmed"] = False
                    context_data.pop("license_plate", None)
                    context_data["part_prompt_retries"] = 0
                    response_text = (
                        "בוא נתחיל נקי 🙂 שלח לי את מספר הרכב (לוחית רישוי) — "
                        "ואני אשלוף אוטומטית את כל פרטי הרכב: יצרן, דגם, שנה ומנוע. "
                        "אחר כך רק תגיד לי איזה חלק אתה צריך."
                    )
            else:
                response_text = _human_recovery_reply(message, preferred_lang=preferred_lang)
    except Exception:
        pass

    # Format response through Gemini for customer-facing channels
    # Skip reformatting when response contains a structured numbered list or payment link
    _skip_format = (
        bool(context_data.get("pending_checkout_parts"))
        or response_text.startswith("*[1].")
        or response_text.startswith("מעולה! הנה קישור")
        or response_text.startswith("ממתاز! إليك")
        or response_text.startswith("ممتاز! إليك")
        or response_text.startswith("Great! Here's your")
        or "autosparefinder.co.il" in response_text
    )
    if source in ("telegram", "whatsapp", "web") and not _skip_format and agent_name not in ("parts_finder_agent",):
        response_text = await _format_response_for_customer(
            response_text, agent_name, source, history
        )

    # Payment-link guard (added 2026-07-05): the LLM once hallucinated a fake
    # Stripe URL ("cs_test_1234567890abcdef") when a customer confirmed a
    # purchase outside the numbered-choice path. Real links are ONLY produced
    # by the create_checkout_link code branch (intent == "{source}_checkout").
    # Any payment-looking URL from any other path is fabricated — strip it.
    _intent_now = str(route_result.get("intent") or "")
    if not _intent_now.endswith("_checkout") and re.search(
        r"(pay\.stripe\.com|checkout\.stripe\.com|stripe\.com/pay)", response_text or ""
    ):
        print(f"[PaymentGuard] Stripped hallucinated payment link from {agent_name} reply")
        if context_data.get("pending_checkout_parts"):
            response_text = "כדי לקבל קישור תשלום מאובטח, שלח את מספר החלק שבחרת — 1, 2 או 3 🙂"
        else:
            response_text = (
                "לפני התשלום צריך לבחור חלק 🙂 תגיד לי איזה חלק אתה צריך, "
                "אציג אפשרויות ומחירים, ואז אשלח קישור תשלום מאובטח."
            )

    response_text = _strip_leaked_reasoning(response_text)
    response_text = _sanitize_internal_pricing_disclosure(response_text)

    db.add(AgentAction(
        message_id=user_msg.id,
        agent_name=route_result.get("agent", agent_name),
        action_type="routing_decision",
        action_data={
            "source": source,
            "conversation_id": str(conversation.id),
            "parts_flow_active": bool(parts_flow_active),
            "model_used": model_used,
            "route_result": route_result,
        },
        success=agent_error is None,
        error_message=agent_error,
        execution_time_ms=exec_ms,
    ))

    # Persist state updates for this turn.
    conversation.context = context_data
    conversation.current_agent = agent_name
    conversation.last_message_at = datetime.utcnow()

    # ── 6. Save assistant message ─────────────────────────────────────────────
    assistant_msg = Message(
        conversation_id=conversation.id,
        role="assistant",
        agent_name=agent_name,
        content=response_text,
        content_type="text",
        model_used=model_used,
    )
    db.add(assistant_msg)
    await db.flush()  # ensure assistant_msg.id is set before AgentAction

    # ── 7. Save agent action log ──────────────────────────────────────────────
    action = AgentAction(
        message_id=assistant_msg.id,
        agent_name=agent_name,
        action_type="respond",
        action_data={
            "source": source,
            "route_result": route_result,
            "conversation_id": str(conversation.id),
            "response_preview": _compact_preview(response_text),
        },
        success=agent_error is None,
        error_message=agent_error,
        execution_time_ms=exec_ms,
    )
    db.add(action)

    memory_updates = _extract_shared_memory_updates(context_data, agent_name)
    memory_keys_updated = await _save_shared_memory_updates(
        db=db,
        user_id=str(user_id),
        conversation_id=str(conversation.id),
        updates=memory_updates,
    )
    memory_keys_used = [item.get("memory_key") for item in shared_memory_rows if item.get("memory_key")]

    await _log_agent_usage_event(
        db=db,
        user_id=str(user_id),
        conversation_id=str(conversation.id),
        message_id=str(assistant_msg.id),
        agent_name=agent_name,
        source=source,
        model_used=model_used,
        route_result=route_result,
        execution_time_ms=exec_ms,
        memory_keys=sorted(set(memory_keys_used + memory_keys_updated)),
        success=agent_error is None,
        error_message=agent_error,
    )

    await db.commit()
    await db.refresh(assistant_msg)

    return {
        "conversation_id": str(conversation.id),
        "message_id": str(assistant_msg.id),
        "agent": agent_name,
        "response": response_text,
        "created_at": assistant_msg.created_at.isoformat(),
        "routing": route_result,
        "shared_memory_keys": sorted(set(memory_keys_used + memory_keys_updated)),
    }
