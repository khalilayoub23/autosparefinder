"""
Script: services/supplier_sourcing.py
Purpose: NIR's supplier-sourcing "superpower" — discover new sellers on the web,
    evaluate them, and onboard the good ones into the `suppliers` table so their
    offers can enrich search + the price-comparison, and orders can route to them.

Process:
  1. discover_sellers(query)  — REAL web search. Primary: Gemini Google-Search
     grounding (hf_client.gemini_web_search). Fallback (when Gemini is 429/quota):
     an LLM proposes candidate domains (Cerebras/hf_text) which are then VERIFIED
     by an actual HTTP fetch of the domain — never fabricated.
  2. evaluate_seller()        — score a candidate from real signals (ships to IL,
     has an API/affiliate/price feed, https, is a live parts store).
  3. onboard_seller()         — dedupe (by name + domain) and INSERT into `suppliers`
     as is_active=FALSE / status=pending_review, with sourcing metadata in
     credentials JSONB. Owner approves activation (approve_supplier) — nothing
     unverified goes live in customer-facing compare on its own.
  4. run_sourcing_cycle()     — drive discovery from gap signals (explicit queries or
     top search_misses), onboard candidates as pending, return a summary for the
     owner console / loop to surface for approval.

Data Modified: `suppliers` (INSERT/UPDATE only; never customer-facing prices here —
    real prices arrive later via a connector/importer once the seller is approved).
Data Sources: Gemini Google-Search grounding; live domain fetches for verification.
Missing Data Delegation: sellers that need a B2B account/token (Turn14, Keystone,
    ASAP, …) are onboarded as status=pending_credentials with `needs` describing the
    exact owner step; they stay is_active=FALSE until the owner supplies the credential.
Last Updated: 2026-07-26
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_IL_SHIP_SIGNALS = (
    "ship to israel", "ships to israel", "international shipping", "worldwide shipping",
    "ship worldwide", "ships worldwide", "israel", "ישראל",  # ישראל
)
_PARTS_SIGNALS = (
    "auto parts", "car parts", "spare parts", "oem", "aftermarket", "automotive",
    "brake", "filter", "engine", "suspension", "חלפים",  # חלפים
)
_API_SIGNALS = ("api", "dropship", "drop ship", "affiliate", "data feed", "price feed", "csv feed", "ftps")


def _db_url() -> str:
    return (os.environ.get("DATABASE_URL", "") or "").replace("postgresql+asyncpg://", "postgresql://")


async def _connect():
    import asyncpg
    return await asyncpg.connect(_db_url())


def _domain(url_or_domain: str) -> str:
    """Registrable-ish domain, lowercased, no scheme/www/path."""
    s = (url_or_domain or "").strip().lower()
    if not s:
        return ""
    if "://" not in s:
        s = "https://" + s
    host = urlparse(s).netloc or ""
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def _extract_domains(text: str) -> list[str]:
    out = []
    for m in re.findall(r"\b([a-z0-9][a-z0-9\-]{1,62}(?:\.[a-z0-9\-]{2,63})+)\b", (text or "").lower()):
        d = _domain(m)
        # skip obvious non-seller / infra domains
        if d and d not in out and not any(
            b in d for b in ("google.", "wikipedia.", "youtube.", "facebook.", "instagram.",
                             "gstatic.", "vertexaisearch", "schema.org", "example.")):
            out.append(d)
    return out


# ────────────────────────────────────────────────────────────────────────────
# 1. DISCOVERY (real web search)
# ────────────────────────────────────────────────────────────────────────────
async def _verify_domain(domain: str, timeout: float = 8.0) -> dict:
    """
    Best-effort live fetch to confirm a candidate is a real parts store and pick up
    ships-to-IL / API signals. Never fabricates: on fetch failure returns
    verified=False and the candidate is kept only as 'unverified'.
    """
    import httpx
    info: dict[str, Any] = {"domain": domain, "verified": False, "http_ok": False,
                            "is_parts": False, "ships_il": False, "has_api": False, "title": ""}
    url = f"https://{domain}/"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}) as cx:
            r = await cx.get(url)
            info["http_ok"] = r.status_code < 400
            body = (r.text or "")[:200000].lower()
            mt = re.search(r"<title[^>]*>(.*?)</title>", body, re.S)
            info["title"] = (mt.group(1).strip()[:160] if mt else "")
            info["is_parts"] = any(s in body for s in _PARTS_SIGNALS)
            info["ships_il"] = any(s in body for s in _IL_SHIP_SIGNALS)
            info["has_api"] = any(s in body for s in _API_SIGNALS)
            info["verified"] = info["http_ok"] and info["is_parts"]
    except Exception as e:
        info["error"] = type(e).__name__
    return info


async def discover_sellers(query: str, ship_to: str = "IL", limit: int = 8,
                           verify: bool = True) -> list[dict]:
    """
    Return candidate sellers [{domain,name,reasons,sources,signals,verified}] for a
    query (e.g. a brand/category we lack coverage for). Real data only.
    """
    import hf_client
    candidates: dict[str, dict] = {}
    sources_all: list[dict] = []
    prompt = (
        f"Find online automotive parts sellers/distributors relevant to: '{query}'. "
        f"Requirements: they should ship to {ship_to} (or offer international shipping) and, "
        f"ideally, expose a dropship API, affiliate program, or downloadable price/data feed. "
        f"For each seller give the exact website domain and one short reason. List up to {limit}."
    )

    # Primary: Gemini Google-Search grounding (real). Tolerate quota 429.
    try:
        res = await hf_client.gemini_web_search(prompt, timeout=45.0)
        sources_all = res.get("sources") or []
        for d in _extract_domains(res.get("text", "") + " " + " ".join(s.get("url", "") for s in sources_all)):
            candidates.setdefault(d, {"domain": d, "reasons": [], "sources": []})
        # attach source urls to their domains
        for s in sources_all:
            d = _domain(s.get("url", ""))
            if d in candidates:
                candidates[d]["sources"].append(s.get("url"))
        if res.get("text"):
            for d in candidates:
                candidates[d]["reasons"].append("gemini-grounded")
        logger.info("[sourcing] gemini discovery for %r -> %d candidates", query, len(candidates))
    except Exception as e:
        logger.warning("[sourcing] gemini_web_search unavailable (%s) — falling back to LLM-propose+verify", type(e).__name__)

    # Fallback / augment: LLM proposes known seller domains (no fabrication of prices —
    # only names/domains, which are then VERIFIED by a real fetch below).
    if len(candidates) < 3:
        try:
            txt = await hf_client.hf_text(
                system="You are a car-parts sourcing analyst. Output ONLY a JSON array.",
                prompt=(f"List up to {limit} real online auto-parts sellers/distributors for '{query}' "
                        f"that ship to {ship_to} or internationally. JSON array of objects "
                        f'{{"domain":"...","name":"...","reason":"..."}} — real, well-known domains only.'),
                timeout=40.0,
            )
            arr = _safe_json_array(txt)
            for o in arr:
                d = _domain(o.get("domain") or o.get("name") or "")
                if not d:
                    continue
                c = candidates.setdefault(d, {"domain": d, "reasons": [], "sources": []})
                if o.get("name"):
                    c["name"] = o["name"]
                if o.get("reason"):
                    c["reasons"].append(str(o["reason"])[:200])
                c["reasons"].append("llm-proposed")
        except Exception as e:
            logger.warning("[sourcing] LLM fallback failed: %s", type(e).__name__)

    out = list(candidates.values())[: limit * 2]
    # Verify each candidate with a real fetch
    if verify:
        for c in out:
            sig = await _verify_domain(c["domain"])
            c["signals"] = sig
            c["verified"] = sig.get("verified", False)
            if not c.get("name"):
                c["name"] = sig.get("title") or c["domain"]
    return out[:limit]


def _safe_json_array(text: str) -> list[dict]:
    if not text:
        return []
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        v = json.loads(m.group(0))
        return [x for x in v if isinstance(x, dict)]
    except Exception:
        return []


# ────────────────────────────────────────────────────────────────────────────
# 2. EVALUATION
# ────────────────────────────────────────────────────────────────────────────
def evaluate_seller(candidate: dict) -> float:
    """Heuristic 0..1 reliability/fit score from real signals."""
    sig = candidate.get("signals") or {}
    score = 0.3
    if sig.get("http_ok"):
        score += 0.15
    if sig.get("is_parts"):
        score += 0.2
    if sig.get("ships_il"):
        score += 0.2
    if sig.get("has_api"):
        score += 0.1
    if candidate.get("sources"):
        score += 0.05
    return round(min(score, 0.95), 2)


# ────────────────────────────────────────────────────────────────────────────
# 3. ONBOARDING (dedupe + insert into suppliers)
# ────────────────────────────────────────────────────────────────────────────
async def onboard_seller(conn, *, name: str, website: str, country: str = "Unknown",
                         status: str = "pending_review", reliability: float = 0.5,
                         api_endpoint: Optional[str] = None, needs: Optional[str] = None,
                         reasons: Optional[list] = None, signals: Optional[dict] = None,
                         source: str = "nir_sourcing") -> dict:
    """
    Insert (or update) a seller in `suppliers`, is_active=FALSE until owner approves.
    Dedupe by lower(name) OR domain(website). Returns {supplier_id, created, name}.
    """
    dom = _domain(website)
    existing = await conn.fetchrow(
        """SELECT id::text, name, website, is_active FROM suppliers
           WHERE lower(name)=lower($1)
              OR ($2 <> '' AND lower(regexp_replace(coalesce(website,''),'^https?://(www\\.)?','')) LIKE $3)
           LIMIT 1""",
        name, dom, (dom + "%") if dom else "___none___",
    )
    meta = {
        "source": source,
        "status": status,
        "discovered_at": int(time.time()),
        "reasons": (reasons or [])[:8],
        "signals": signals or {},
    }
    if needs:
        meta["needs"] = needs
    if existing:
        # refresh sourcing metadata; never downgrade an already-active supplier
        await conn.execute(
            """UPDATE suppliers
               SET credentials = coalesce(credentials,'{}'::jsonb) || $2::jsonb,
                   updated_at = NOW()
               WHERE id = $1::uuid""",
            existing["id"], json.dumps(meta),
        )
        return {"supplier_id": existing["id"], "created": False, "name": existing["name"],
                "already_active": existing["is_active"]}

    row = await conn.fetchrow(
        """INSERT INTO suppliers (id, name, country, website, api_endpoint,
                                  credentials, reliability_score, is_active, priority,
                                  created_at, updated_at)
           VALUES (gen_random_uuid(), $1, $2, $3, $4, $5::jsonb, $6, FALSE, 900, NOW(), NOW())
           RETURNING id::text""",
        name, country, ("https://" + dom) if dom else website, api_endpoint,
        json.dumps(meta), reliability,
    )
    return {"supplier_id": row["id"], "created": True, "name": name}


# ────────────────────────────────────────────────────────────────────────────
# 4. CYCLE + OWNER REVIEW
# ────────────────────────────────────────────────────────────────────────────
async def run_sourcing_cycle(queries: Optional[list[str]] = None, max_onboard: int = 6,
                             conn=None) -> dict:
    """
    Discover + evaluate + onboard (pending_review) sellers for a set of gap queries.
    If queries is None, derives them from recent search_misses. Returns a summary.
    """
    own = False
    if conn is None:
        conn = await _connect()
        own = True
    try:
        if not queries:
            queries = await _gap_queries(conn)
        summary = {"queries": queries, "discovered": 0, "onboarded": [], "skipped": 0}
        for q in queries[:6]:
            try:
                cands = await discover_sellers(q, limit=6)
            except Exception as e:
                logger.warning("[sourcing] discover failed for %r: %s", q, type(e).__name__)
                continue
            summary["discovered"] += len(cands)
            # keep the best few real candidates
            ranked = sorted(cands, key=evaluate_seller, reverse=True)
            for c in ranked:
                if len([x for x in summary["onboarded"]]) >= max_onboard:
                    break
                score = evaluate_seller(c)
                if score < 0.55 or not c.get("signals", {}).get("http_ok"):
                    summary["skipped"] += 1
                    continue
                res = await onboard_seller(
                    conn, name=(c.get("name") or c["domain"])[:120], website=c["domain"],
                    country=_guess_country(c), status="pending_review", reliability=score,
                    reasons=c.get("reasons"), signals=c.get("signals"),
                )
                if res.get("created"):
                    summary["onboarded"].append({"name": res["name"], "domain": c["domain"], "score": score})
        return summary
    finally:
        if own:
            await conn.close()


def _guess_country(candidate: dict) -> str:
    d = candidate.get("domain", "")
    if d.endswith(".il") or "israel" in (candidate.get("signals", {}).get("title", "") or "").lower():
        return "IL"
    if d.endswith(".ie"):
        return "IE"
    if d.endswith(".co.uk") or d.endswith(".uk"):
        return "UK"
    if d.endswith(".de"):
        return "DE"
    return "Unknown"


async def _gap_queries(conn) -> list[str]:
    """Derive sourcing queries from recent zero-result customer searches."""
    try:
        rows = await conn.fetch(
            """SELECT query, COUNT(*) c FROM search_misses
               WHERE created_at > NOW() - INTERVAL '30 days' AND coalesce(query,'') <> ''
               GROUP BY query ORDER BY c DESC LIMIT 5""")
        qs = [f"{r['query']} car part supplier" for r in rows if r["query"]]
        if qs:
            return qs
    except Exception:
        pass
    # sensible defaults if no misses table/rows
    return ["performance aftermarket car parts distributor", "OEM car parts dropship supplier"]


async def list_pending(conn=None) -> list[dict]:
    own = False
    if conn is None:
        conn = await _connect(); own = True
    try:
        rows = await conn.fetch(
            """SELECT id::text, name, website, country, reliability_score,
                      credentials->>'status' AS status, credentials->>'needs' AS needs
               FROM suppliers
               WHERE is_active = FALSE AND credentials->>'source' = 'nir_sourcing'
               ORDER BY reliability_score DESC NULLS LAST, created_at DESC""")
        return [dict(r) for r in rows]
    finally:
        if own:
            await conn.close()


async def approve_supplier(id_prefix: str, conn=None) -> dict:
    own = False
    if conn is None:
        conn = await _connect(); own = True
    try:
        row = await conn.fetchrow(
            """UPDATE suppliers
               SET is_active = TRUE,
                   credentials = coalesce(credentials,'{}'::jsonb) || '{"status":"approved"}'::jsonb,
                   updated_at = NOW()
               WHERE id::text LIKE $1 AND credentials->>'source'='nir_sourcing'
               RETURNING name""",
            id_prefix + "%")
        return {"ok": bool(row), "name": row["name"] if row else None}
    finally:
        if own:
            await conn.close()


async def reject_supplier(id_prefix: str, conn=None) -> dict:
    own = False
    if conn is None:
        conn = await _connect(); own = True
    try:
        row = await conn.fetchrow(
            """UPDATE suppliers
               SET credentials = coalesce(credentials,'{}'::jsonb) || '{"status":"rejected"}'::jsonb,
                   is_active = FALSE, updated_at = NOW()
               WHERE id::text LIKE $1 AND credentials->>'source'='nir_sourcing'
               RETURNING name""",
            id_prefix + "%")
        return {"ok": bool(row), "name": row["name"] if row else None}
    finally:
        if own:
            await conn.close()
