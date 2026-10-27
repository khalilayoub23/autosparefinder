"""
Script: policy_guard.py
Purpose: Enforcement of the mandatory customer-reply policy OUTSIDE the model (2026-10-27).

Why this exists: a live SalesAgent reply quoted "shipping ₪29–₪149". That range was never a quote:
it was hard-coded into two agent prompts and four catalog-context templates, and the model repeated
it. Separately, the Groq fallback cut every customer system prompt to its first 5,000 characters,
dropping the PROHIBITED / SHIPPING TRUTH / VERIFY policy sections, and the router let the model pick
"admin only" agents for customers. Prompt wording is not enforcement; this module is.

Process:
  1. fit_request(system, prompt, max_input_tokens)
       Fits a request into a provider's VERIFIED input budget without losing mandatory policy:
       optional [GUIDANCE]...[/GUIDANCE] blocks are shortened first, then the oldest conversation
       lines; the system policy and the CURRENT customer message are never cut. If they alone do
       not fit, PolicyBudgetExceeded is raised (the caller fails safe) — nothing is silently dropped.
  2. validate_customer_reply(reply, grounding_texts)
       Business-critical claims in a customer reply must be grounded in data the application gave
       the model this turn (catalog/order context) or that the customer wrote:
         * every currency amount (₪/ש"ח/NIS/ILS/شيكل/$/€, including both ends of a range);
         * every link (own-domain static pages are allowed; anything else must be in the data).
       Returns the list of violations (empty = compliant).
  3. CUSTOMER_AGENTS: the agents a customer channel may be routed to. Enforced in code by the router.

Data Imported/Modified: none (pure functions).
Data Sources: Groq limit verified live 2026-10-27: x-ratelimit-limit-tokens: 8000 per request window;
              a 13,120-char customer system prompt = 3,149 prompt tokens (HTTP 200).
Missing Data Delegation: n/a.
Last Updated: 2026-10-27
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set, Tuple

# ── routing allowlist ──────────────────────────────────────────────────────────
# Agents a CUSTOMER message may reach. The router prompt calls social_media_manager_agent and
# supplier_manager_agent "admin only", but that was wording only; tech_agent's process() takes a
# different signature and crashes on a customer turn.
CUSTOMER_AGENTS = frozenset({
    "parts_finder_agent", "sales_agent", "orders_agent", "finance_agent",
    "service_agent", "security_agent", "marketing_agent",
})
INTERNAL_AGENTS = frozenset({"router_agent", "tech_agent", "supplier_manager_agent", "social_media_manager_agent"})
CUSTOMER_FALLBACK_AGENT = "service_agent"

# ── request fitting ───────────────────────────────────────────────────────────
GUIDANCE_OPEN = "[GUIDANCE - MAY BE SHORTENED]"
GUIDANCE_CLOSE = "[/GUIDANCE]"
_GUIDANCE_RE = re.compile(re.escape(GUIDANCE_OPEN) + r".*?" + re.escape(GUIDANCE_CLOSE), re.S)
_GUIDANCE_SHORT = "[GUIDANCE omitted for length — all MANDATORY rules above and below still apply]"
_HISTORY_OMITTED = "[earlier conversation omitted for length]"

# Conservative: the live measurement was 4.17 chars/token; Hebrew/Arabic-heavy text is denser.
CHARS_PER_TOKEN = 3.0


class PolicyBudgetExceeded(RuntimeError):
    """The mandatory system policy + the current customer message do not fit the provider budget.
    Callers must fail safe (next provider / localized holding reply), never truncate the policy."""


def estimate_tokens(text: str) -> int:
    return int(len(text or "") / CHARS_PER_TOKEN) + 1


@dataclass
class FitInfo:
    guidance_removed: bool = False
    history_lines_dropped: int = 0
    system_tokens: int = 0
    prompt_tokens: int = 0
    notes: List[str] = field(default_factory=list)


def _split_current(prompt: str) -> Tuple[List[str], str]:
    """Split a flattened "role: content" prompt into earlier lines and the CURRENT (last) user turn."""
    idx = prompt.rfind("\nuser:")
    if prompt.startswith("user:") and idx < 0:
        return [], prompt
    if idx < 0:
        return [], prompt                     # unknown shape: treat the whole prompt as current
    return prompt[:idx].split("\n"), prompt[idx + 1:]


def fit_request(system: str, prompt: str, max_input_tokens: int) -> Tuple[str, str, FitInfo]:
    info = FitInfo()
    system = system or ""
    prompt = prompt or ""

    def total(s: str, p: str) -> int:
        return estimate_tokens(s) + estimate_tokens(p)

    if total(system, prompt) <= max_input_tokens:
        info.system_tokens, info.prompt_tokens = estimate_tokens(system), estimate_tokens(prompt)
        return system, prompt, info

    # 1. optional guidance blocks
    if _GUIDANCE_RE.search(system):
        system = _GUIDANCE_RE.sub(_GUIDANCE_SHORT, system)
        info.guidance_removed = True
        info.notes.append("guidance_shortened")

    # 2. oldest conversation lines (never the current customer message)
    if total(system, prompt) > max_input_tokens:
        earlier, current = _split_current(prompt)
        while earlier and total(system, "\n".join([_HISTORY_OMITTED] + earlier + [current])) > max_input_tokens:
            earlier.pop(0)
            info.history_lines_dropped += 1
        prompt = "\n".join(([_HISTORY_OMITTED] if info.history_lines_dropped else []) + earlier + [current])
        if info.history_lines_dropped:
            info.notes.append(f"history_lines_dropped={info.history_lines_dropped}")

    # 3. mandatory policy + current message must fit, or the request is refused
    if total(system, prompt) > max_input_tokens:
        raise PolicyBudgetExceeded(
            f"mandatory system policy ({estimate_tokens(system)} tok est) + current message "
            f"({estimate_tokens(prompt)} tok est) exceed the provider budget ({max_input_tokens} tok)"
        )
    info.system_tokens, info.prompt_tokens = estimate_tokens(system), estimate_tokens(prompt)
    return system, prompt, info


# ── grounding of business-critical claims ──────────────────────────────────────
_NUM = r"\d{1,3}(?:[,٬]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_CUR_AFTER = r"(?:₪|ש\"ח|ש״ח|שח\b|שקל(?:ים)?|NIS\b|ILS\b|shekels?\b|شيكل|شيقل|شواقل|\$|USD\b|€|EUR\b|euros?\b)"
_CUR_BEFORE = r"(?:₪|\$|€|NIS|ILS|USD|EUR)"
_DASH = r"\s*[-–—\u2011\u2010]\s*"
_RE_RANGE_BEFORE = re.compile(rf"{_CUR_BEFORE}\s*({_NUM}){_DASH}{_CUR_BEFORE}?\s*({_NUM})", re.I)
_RE_RANGE_AFTER = re.compile(rf"({_NUM}){_DASH}({_NUM})\s*{_CUR_AFTER}", re.I)
_RE_BEFORE = re.compile(rf"{_CUR_BEFORE}\s*({_NUM})", re.I)
_RE_AFTER = re.compile(rf"({_NUM})\s*{_CUR_AFTER}", re.I)
_RE_ANY_NUMBER = re.compile(_NUM)
_RE_URL = re.compile(r"(?:https?://|www\.)[^\s)\]>\"'،]+|\bautosparefinder\.co\.il[^\s)\]>\"'،]*", re.I)

OWN_DOMAIN = "autosparefinder.co.il"
# Static own-domain pages a reply may always point to. Payment (/pay/...) and order links are
# produced by backend code only, so they must appear in the turn's data to be allowed.
OWN_STATIC_PATHS = ("", "/", "/cart", "/search", "/parts", "/api/v1/go", "/contact", "/terms", "/privacy")


def _to_float(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", "").replace("٬", ""))
    except ValueError:
        return None


def currency_amounts(text: str) -> List[float]:
    """Every currency amount stated in `text`, both ends of ranges included."""
    out: List[float] = []
    t = text or ""
    for rx in (_RE_RANGE_BEFORE, _RE_RANGE_AFTER):
        for m in rx.finditer(t):
            out += [v for v in (_to_float(m.group(1)), _to_float(m.group(2))) if v is not None]
    for rx in (_RE_BEFORE, _RE_AFTER):
        for m in rx.finditer(t):
            v = _to_float(m.group(1))
            if v is not None:
                out.append(v)
    return out


def grounded_numbers(texts: Iterable[str]) -> Set[float]:
    nums: Set[float] = set()
    for t in texts:
        for m in _RE_ANY_NUMBER.finditer(t or ""):
            v = _to_float(m.group(0))
            if v is not None:
                nums.add(v)
    return nums


def _is_grounded(value: float, grounded: Set[float]) -> bool:
    # displayed prices are rounded (f"{x:.0f}") — allow rounding to the shekel
    return any(abs(value - g) <= 1.0 for g in grounded)


def _normalize_url(u: str) -> str:
    u = u.rstrip(".,;:!?)").lower()
    u = re.sub(r"^https?://", "", u)
    return re.sub(r"^www\.", "", u)


def _url_allowed(url: str, grounding_blob: str) -> bool:
    n = _normalize_url(url)
    if n and n in _normalize_url(grounding_blob):
        return True
    if n.startswith(OWN_DOMAIN):
        path = n[len(OWN_DOMAIN):].split("?")[0].split("#")[0].rstrip("/")
        return any(path == p.rstrip("/") or (p not in ("", "/") and path.startswith(p.rstrip("/") + "/"))
                   for p in OWN_STATIC_PATHS) or path == ""
    return False


# Shipping is part of every offer's total (owner rule 2026-10-27). A reply must never present the
# delivery COST as unknown until checkout/payment — that reads as a second, later charge.
_SAME_SENTENCE = r"[^.!?\n؟]{0,90}"
_RE_SHIPPING_DEFERRED = re.compile(
    r"(?:(?:shipping|delivery)\s+(?:cost|costs|fee|fees|price|charge|charges)|cost\s+of\s+(?:shipping|delivery))"
    + _SAME_SENTENCE +
    r"(?:at\s+checkout|during\s+checkout|at\s+(?:the\s+)?payment|after\s+(?:the\s+)?payment|on\s+the\s+payment\s+page|later|separately)"
    r"|(?:עלות|מחיר|דמי)\s+(?:ה)?משלוח" + _SAME_SENTENCE +
    r"(?:בקופה|בתשלום|לאחר\s+התשלום|אחרי\s+התשלום|בעמוד\s+התשלום|בסיום\s+ההזמנה|בנפרד|בהמשך)"
    r"|(?:تكلفة|سعر|رسوم|أجرة)\s+(?:ال)?(?:شحن|توصيل)" + _SAME_SENTENCE +
    r"(?:عند\s+الدفع|بعد\s+الدفع|عند\s+إتمام\s+الطلب|في\s+صفحة\s+الدفع|عند\s+الطلب|بشكل\s+منفصل|لاحقا|لاحقاً)",
    re.IGNORECASE,
)


# Delivery-TIME claims ("usually 2-5 business days") must come from the turn's data like amounts do
# (owner 2026-10-27: an Arabic reply invented "2‑5 أيام عمل"). Only sentences about delivery/arrival are
# judged; return/refund/warranty periods are policy facts, not delivery claims.
_RE_SENTENCE = re.compile(r"[^.!?\n؟]+")
_RE_DELIVERY_CTX = re.compile(
    r"deliver|arriv|shipping|ships?\b|transit|courier|משלוח|אספקה|יגיע|תגיע|הגעה|שילוח|توصيل|شحن|الشحن|يصل|تصل|وصول",
    re.IGNORECASE)
_RE_POLICY_CTX = re.compile(r"return|refund|warrant|החזר|החזרה|אחריות|ביטול|إرجاع|استرجاع|استرداد|ضمان", re.IGNORECASE)
_RE_DAYS = re.compile(
    r"(\d+(?:\.\d+)?)(?:\s*[-–—\u2011\u2010]\s*(\d+(?:\.\d+)?))?\s*"
    r"(?:(?:business|working|calendar)\s+)?(?:days?|weeks?|ימי\s+עסקים|ימים|יום|שבועות|שבוע|أيام|يوم|يوما|يومًا|أسابيع|أسبوع)",
    re.IGNORECASE)


def delivery_time_claims(text: str):
    """Numbers stated as a delivery duration (both ends of a range), from delivery sentences only."""
    out = []
    for sent in _RE_SENTENCE.findall(text or ""):
        if not _RE_DELIVERY_CTX.search(sent) or _RE_POLICY_CTX.search(sent):
            continue
        for m in _RE_DAYS.finditer(sent):
            out += [float(x) for x in m.groups() if x]
    return out


@dataclass
class Violation:
    kind: str          # "empty" | "ungrounded_amount" | "ungrounded_link" | "shipping_deferred" | "ungrounded_delivery_time"
    detail: str


def validate_customer_reply(reply: str, grounding_texts: Iterable[str]) -> List[Violation]:
    """Check a customer-facing reply against the data the application supplied this turn."""
    grounding = [g for g in grounding_texts if g]
    if not (reply or "").strip():
        return [Violation("empty", "")]
    violations: List[Violation] = []
    grounded = grounded_numbers(grounding)
    for amount in currency_amounts(reply):
        if not _is_grounded(amount, grounded):
            violations.append(Violation("ungrounded_amount", f"{amount:g}"))
    for days in delivery_time_claims(reply):
        if not any(abs(days - g) < 0.01 for g in grounded):
            violations.append(Violation("ungrounded_delivery_time", f"{days:g}"))
    m_ship = _RE_SHIPPING_DEFERRED.search(reply or "")
    if m_ship:
        violations.append(Violation("shipping_deferred", m_ship.group(0)[:60]))
    blob = "\n".join(grounding)
    for m in _RE_URL.finditer(reply or ""):
        if not _url_allowed(m.group(0), blob):
            violations.append(Violation("ungrounded_link", _normalize_url(m.group(0))[:80]))
    return violations


def rewrite_preserves_facts(raw: str, rewritten: str) -> List[Violation]:
    """A formatting pass may rephrase, never add numbers or links that were not in the raw reply."""
    v: List[Violation] = []
    raw_nums = grounded_numbers([raw])
    for m in _RE_ANY_NUMBER.finditer(rewritten or ""):
        val = _to_float(m.group(0))
        if val is not None and not _is_grounded(val, raw_nums):
            v.append(Violation("ungrounded_amount", m.group(0)))
    for m in _RE_URL.finditer(rewritten or ""):
        if _normalize_url(m.group(0)) not in _normalize_url(raw):
            v.append(Violation("ungrounded_link", _normalize_url(m.group(0))[:80]))
    return v


def correction_note(violations: List[Violation]) -> str:
    kinds = sorted({x.kind for x in violations})
    parts = []
    if "ungrounded_amount" in kinds:
        parts.append("Your draft stated a price or shipping amount that is not in the data provided. "
                     "Use ONLY amounts that appear in the catalog/order data of this conversation. Delivery is "
                     "already included in each part's total price; without a part, offer to find it.")
    if "ungrounded_link" in kinds:
        parts.append("Your draft contained a link that the system did not provide. Use only links "
                     "that appear in the data, or https://autosparefinder.co.il/cart.")
    if "ungrounded_delivery_time" in kinds:
        parts.append("Your draft stated a delivery time that is not in the data. Never give a typical or "
                     "generic delivery window; delivery time depends on the supplier and is shown with each "
                     "part when known. Offer to find the part.")
    if "shipping_deferred" in kinds:
        parts.append("Your draft said the shipping cost is known only at checkout/payment. That is wrong: "
                     "delivery is already included in each part's total price and charged once with the part. "
                     "Say that, and offer to find the part for the customer's car.")
    if "empty" in kinds:
        parts.append("Your draft was empty. Write the reply.")
    return "\n\n[SYSTEM NOTE] " + " ".join(parts) if parts else ""
