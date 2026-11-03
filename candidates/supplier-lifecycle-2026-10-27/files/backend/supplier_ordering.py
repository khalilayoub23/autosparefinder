"""
Script: supplier_ordering.py
Purpose: The ONE contract every supplier order connection fills in (owner decision 2026-10-27: "build it now and
         prepare it for the suppliers"). procurement.submit_po is the only caller, and it calls a connector only
         after the owner's purchase_order approval for the exact current terms (supplier_authorization).
Process:
  OrderConnector            mode + place_order(row) -> OrderOutcome | raises AmbiguousSubmission
                            + get_status(reference) -> {"found", "status", "tracking"} (reconciliation)
  connector_for(supplier)   chooses the connection for a supplier:
      AliExpressDsConnector         AliExpress DS API (aliexpress.ds.order.create - VERIFIED against the official
                                    reference 2026-10-27, not yet against a live order). Before ordering it re-reads
                                    the product live and picks exactly ONE SKU (USD, stock >= quantity, price <= our
                                    stored price) and the cheapest TRACKED delivery within the approved shipping;
                                    otherwise it refuses. out_order_id = procurement id (AliExpress rejects a repeat).
                                    try_to_pay is always false; payment = pay_order (afterpay), separately approved.
      EmailPurchaseOrderConnector   suppliers marked credentials.ordering = "email" with a VERIFIED order contact:
                                    a fixed purchase-order email through GmailClient.send_purchase_order_email
                                    (its own endpoint set + PurchaseOrderEmailAuthorization), draft read back first.
                                    "Submitted" means the email was sent - the supplier must still confirm.
      AwaitingCredentialsConnector  dropship suppliers registered pending_credentials (ASAP, Turn14, Keystone, Meyer,
                                    ATD): refuses with the owner step from credentials.needs.
      ManualConnector               everyone else: refuses - the owner orders on the supplier's site and records it
                                    with הוזמןידנית.
  Master switch SUPPLIER_AUTO_ORDER_ENABLED (default 0): while off, EVERY supplier is manual.
  Ambiguity: a request that may have reached the supplier (timeout after sending, 5xx, unreadable reply) raises
  AmbiguousSubmission -> procurement marks EXCEPTION and never resubmits automatically. A definite refusal
  (validation, error_response, is_success=false, connection never opened) returns success=False.
Data Imported/Modified: none directly (procurement records the outcome); Gmail draft + one send for email POs.
Data Sources: AliExpress Open Platform (api-sg.aliexpress.com/sync), supplier_parts / suppliers (catalog DB),
         orders.shipping_address (PII DB) for the ship-to address of a dropship order.
Last Updated: 2026-10-27
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class OrderOutcome:
    success: bool
    order_id: Optional[str] = None
    error_message: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)


class AmbiguousSubmission(Exception):
    """The supplier may or may not have received the order: reconcile, never resubmit blindly."""


def auto_order_enabled() -> bool:
    import supplier_flags
    return supplier_flags.enabled("SUPPLIER_AUTO_ORDER_ENABLED")


class OrderConnector:
    mode = "manual"
    name = "manual"

    async def place_order(self, row: Dict[str, Any]) -> OrderOutcome:
        raise NotImplementedError

    async def get_status(self, reference: str) -> Dict[str, Any]:
        return {"found": False, "status": None, "tracking": None, "reason": f"{self.mode}: no status API"}


class ManualConnector(OrderConnector):
    def __init__(self, reason: str = "no ordering API for this supplier"):
        self.reason = reason

    async def place_order(self, row):
        return OrderOutcome(False, error_message=f"manual ordering: {self.reason} - order on the supplier's site, "
                                                 "then record it with הוזמןידנית <id> <supplier order number>")


class AwaitingCredentialsConnector(OrderConnector):
    mode = "awaiting_credentials"
    name = "awaiting_credentials"

    def __init__(self, needs: str):
        self.needs = needs or "supplier account / API credentials"

    async def place_order(self, row):
        return OrderOutcome(False, error_message=f"ordering connection waits for credentials: {self.needs}")


# ── AliExpress DS ──────────────────────────────────────────────────────────────────────────────────────
_AE_ITEM = re.compile(r"/item/(\d{8,20})\.html")


def ae_product_id(url: str) -> Optional[str]:
    m = _AE_ITEM.search(url or "")
    return m.group(1) if m else None


def _num(v) -> float:
    m = re.search(r"\d+(?:\.\d+)?", str(v or "").replace(",", ""))
    return float(m.group()) if m else 0.0


def choose_sku(skus: List[Dict[str, Any]], quantity: int, max_price_usd: float) -> Dict[str, Any]:
    """Exactly one orderable variant or a refusal reason. Never guesses between variants."""
    usable = []
    for s in skus or []:
        if str(s.get("currency_code") or "USD").upper() != "USD":
            continue
        price = _num(s.get("offer_sale_price")) or _num(s.get("offer_bulk_sale_price")) or _num(s.get("sku_price"))
        stock = int(_num(s.get("sku_available_stock")))
        if price <= 0 or stock < quantity:
            continue
        usable.append({"sku_attr": str(s.get("sku_attr") or ""), "sku_id": str(s.get("sku_id") or ""),
                       "price_usd": round(price, 2), "stock": stock})
    if not usable:
        return {"ok": False, "reason": "no variant in stock in USD for this quantity"}
    exact = [u for u in usable if abs(u["price_usd"] - round(max_price_usd, 2)) < 0.005]
    pick = exact if len(exact) == 1 else (usable if len(usable) == 1 else [])
    if len(exact) > 1:
        return {"ok": False, "reason": f"{len(exact)} variants at the approved price - cannot tell which one"}
    if not pick:
        return {"ok": False, "reason": f"{len(usable)} variants and none matches the approved price exactly"}
    if pick[0]["price_usd"] > round(max_price_usd, 2) + 0.005:
        return {"ok": False, "reason": f"price increased at supplier: {pick[0]['price_usd']} > {max_price_usd}"}
    return {"ok": True, **pick[0]}


def ae_address(shipping: Dict[str, Any]) -> Dict[str, Any]:
    phone = re.sub(r"[^\d]", "", str(shipping.get("phone") or ""))
    if phone.startswith("972"):
        phone = phone[3:]
    phone = phone.lstrip("0")
    country = str(shipping.get("country") or "").strip().upper()
    country = {"ISRAEL": "IL", "ישראל": "IL"}.get(country, country)
    out = {"full_name": str(shipping.get("full_name") or "").strip(), "address": str(shipping.get("street") or "").strip(),
           "city": str(shipping.get("city") or "").strip(), "province": str(shipping.get("city") or "").strip(),
           "zip": str(shipping.get("postal_code") or "").strip(), "country": country,
           "mobile_no": phone, "phone_country": "+972" if country == "IL" else ""}
    missing = [k for k in ("full_name", "address", "city", "country", "mobile_no") if not out[k]]
    if missing:
        raise ValueError(f"ship-to address incomplete: {missing}")
    return out


def _true(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


def choose_delivery(options: List[Dict[str, Any]], max_fee_usd: float) -> Dict[str, Any]:
    """Cheapest TRACKED delivery option whose fee does not exceed what the owner approved."""
    tracked = []
    for o in options or []:
        if not _true(o.get("tracking")) or not o.get("code"):
            continue
        if str(o.get("shipping_fee_currency") or "USD").upper() != "USD":
            continue
        # free only when the API SAYS free; a missing / unreadable fee is NOT free (2026-10-27 safety gate)
        if _true(o.get("free_shipping")):
            fee = 0.0
        else:
            raw = str(o.get("shipping_fee_cent") if o.get("shipping_fee_cent") is not None else "").strip()
            if not re.fullmatch(r"\d+(?:\.\d+)?", raw):
                continue
            fee = float(raw)
        tracked.append({"code": str(o["code"]), "fee_usd": round(fee, 2),
                        "max_days": int(_num(o.get("max_delivery_days"))) or None})
    if not tracked:
        return {"ok": False, "reason": "no tracked delivery option to Israel"}
    best = min(tracked, key=lambda x: x["fee_usd"])
    if best["fee_usd"] > max_fee_usd + 0.005:
        return {"ok": False, "reason": f"cheapest tracked shipping ${best['fee_usd']} exceeds the approved ${max_fee_usd}"}
    return {"ok": True, **best}


class AliExpressDsConnector(OrderConnector):
    """AliExpress DS ordering. Methods + fields VERIFIED against the official Open Platform reference
    (openservice.aliexpress.com, AE-Dropshipper, read 2026-10-27): aliexpress.ds.order.create (out_order_id =
    our idempotency key; REPEATED_ORDER_ERROR on a duplicate), aliexpress.ds.freight.query,
    aliexpress.ds.order.afterpay, aliexpress.ds.order.tracking.get, aliexpress.trade.ds.order.get.
    ds.order.create is "create AND pay": try_to_pay is ALWAYS "false" here - paying is the separate
    supplier_payment approval (pay_order -> afterpay). Not yet exercised against the live API (first live order
    needs the owner's approval)."""
    mode = "api"
    name = "aliexpress_ds"
    can_pay = True

    def __init__(self, supplier=None, http_post=None, address_loader=None, offer_loader=None, usd_rate=None):
        self._sup = supplier
        self._post = http_post            # async (data) -> (status, json)  - injectable for tests
        self._address = address_loader    # async (order_id) -> shipping_address dict
        self._offer = offer_loader        # async (supplier_part_id) -> {"supplier_url", "price_usd"}
        self._rate = usd_rate             # async () -> ILS per USD

    def _supplier(self):
        if self._sup is None:
            from services.suppliers.aliexpress_supplier import AliExpressSupplier
            self._sup = AliExpressSupplier()
        return self._sup

    async def _http(self, data: Dict[str, Any]):
        if self._post:
            return await self._post(data)
        import httpx
        from services.suppliers.aliexpress_supplier import ALIEXPRESS_API_URL
        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.post(ALIEXPRESS_API_URL, data=data)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:      # never reached AliExpress
            return 0, {"_transport_error": type(e).__name__}
        except httpx.HTTPError as e:                                  # sent; outcome unknown
            raise AmbiguousSubmission(f"AliExpress request outcome unknown: {type(e).__name__}") from None
        try:
            return r.status_code, r.json()
        except ValueError:
            if r.status_code >= 500:
                raise AmbiguousSubmission(f"AliExpress HTTP {r.status_code} without a readable body") from None
            return r.status_code, {}

    async def _call(self, method: str, extra: Dict[str, Any]) -> Dict[str, Any]:
        sup = self._supplier()
        if not await sup._ensure_token():
            return {"_error": "AliExpress credentials not ready (token)"}
        status, data = await self._http(sup._build_request(method, extra))
        if data.get("_transport_error"):
            return {"_error": f"AliExpress unreachable ({data['_transport_error']}) - nothing sent"}
        return {"_status": status, **data}

    @staticmethod
    def _result(data: Dict[str, Any], method: str) -> Any:
        return (data.get(method.replace(".", "_") + "_response") or {}).get("result")

    async def _skus(self, product_id: str) -> List[Dict[str, Any]]:
        data = await self._call("aliexpress.ds.product.get", {"product_id": int(product_id), "ship_to_country": "IL",
                                                               "target_currency": "USD", "target_language": "EN"})
        if data.get("_error") or data.get("error_response"):
            raise ValueError(data.get("_error") or f"product.get error {data['error_response'].get('code')}")
        res = self._result(data, "aliexpress.ds.product.get") or {}
        return ((res.get("ae_item_sku_info_dtos") or {}).get("ae_item_sku_info_d_t_o")) or []

    async def _delivery(self, product_id: str, sku_id: str, qty: int) -> List[Dict[str, Any]]:
        req = {"quantity": str(qty), "shipToCountry": "IL", "productId": str(product_id), "selectedSkuId": sku_id,
               "language": "en_US", "currency": "USD", "locale": "en_US"}
        data = await self._call("aliexpress.ds.freight.query", {"queryDeliveryReq": json.dumps(req)})
        if data.get("_error") or data.get("error_response"):
            raise ValueError(data.get("_error") or f"freight.query error {data['error_response'].get('code')}")
        res = self._result(data, "aliexpress.ds.freight.query") or {}
        if not _true(res.get("success")):
            raise ValueError(f"freight.query: {res.get('code')} {str(res.get('msg'))[:120]}")
        return res.get("delivery_options") or []

    async def _max_shipping_usd(self, row: Dict[str, Any]) -> float:
        """The shipping the owner approved (terms), in USD. Unknown approved shipping -> the explicit cap
        ALIEXPRESS_DS_MAX_SHIPPING_USD (default 0 = free shipping only)."""
        ship = (row.get("terms") or {}).get("shipping") or {}
        cost = ship.get("cost")
        try:
            ils = float(cost) if cost not in (None, "None", "") else None
        except (TypeError, ValueError):
            ils = None
        if ils is None or not self._rate:
            return float(os.getenv("ALIEXPRESS_DS_MAX_SHIPPING_USD", "0") or 0)
        return round(ils / float(await self._rate()), 2)

    async def place_order(self, row: Dict[str, Any]) -> OrderOutcome:
        items = row.get("items") or []
        try:
            address = ae_address(await self._address(row["order_id"]))
            max_ship = await self._max_shipping_usd(row)
            lines, picks = [], []
            for it in items:
                offer = await self._offer(it["supplier_part_id"])
                pid = ae_product_id((offer or {}).get("supplier_url") or "")
                if not pid:
                    return OrderOutcome(False, error_message=f"offer {it['supplier_part_id'][:8]} has no AliExpress product id")
                qty = int(it["quantity"])
                pick = choose_sku(await self._skus(pid), qty, float((offer or {}).get("price_usd") or 0))
                if not pick["ok"]:
                    return OrderOutcome(False, error_message=f"product {pid}: {pick['reason']}")
                dlv = choose_delivery(await self._delivery(pid, pick["sku_id"], qty), max_ship)
                if not dlv["ok"]:
                    return OrderOutcome(False, error_message=f"product {pid}: {dlv['reason']}")
                lines.append({"product_id": pid, "product_count": str(qty), "sku_attr": pick["sku_attr"],
                              "logistics_service_name": dlv["code"], "order_memo": f"ASF {row['id'][:8]}"})
                picks.append({"product_id": pid, "sku_id": pick["sku_id"], "price_usd": pick["price_usd"],
                              "delivery": dlv})
        except ValueError as e:
            return OrderOutcome(False, error_message=str(e)[:300])
        dto = {"product_items": lines, "logistics_address": address, "out_order_id": row["id"]}
        ext = {"payment": {"try_to_pay": "false", "pay_currency": "USD"}}      # NEVER pay here
        method = "aliexpress.ds.order.create"
        data = await self._call(method, {"param_place_order_request4_open_api_d_t_o": json.dumps(dto),
                                         "ds_extend_request": json.dumps(ext)})
        if data.get("_error"):
            return OrderOutcome(False, error_message=data["_error"])
        if data.get("error_response"):
            er = data["error_response"]
            return OrderOutcome(False, error_message=f"AliExpress refused: {er.get('code')} {str(er.get('msg'))[:160]}")
        if int(data.get("_status") or 0) >= 500:
            raise AmbiguousSubmission(f"AliExpress HTTP {data.get('_status')}")
        res = self._result(data, method)
        if not isinstance(res, dict):
            raise AmbiguousSubmission("AliExpress reply has no result block - order state unknown")
        if str(res.get("error_code") or "").upper() == "REPEATED_ORDER_ERROR":
            raise AmbiguousSubmission("AliExpress already has an order with this out_order_id - reconcile")
        orders = [str(o) for o in (res.get("order_list") or [])]
        if _true(res.get("is_success")) and orders:
            return OrderOutcome(True, order_id=",".join(orders),
                                evidence={"connector": self.name, "method": method, "order_list": orders,
                                          "out_order_id": row["id"], "lines": picks, "paid": False})
        if _true(res.get("is_success")) and not orders:
            raise AmbiguousSubmission("AliExpress reported success without an order number")
        return OrderOutcome(False, error_message=f"AliExpress: {res.get('error_code')} {str(res.get('error_msg'))[:160]}")

    async def pay_order(self, reference: str) -> OrderOutcome:
        """aliexpress.ds.order.afterpay for each AliExpress order of this purchase. REAL MONEY: called only by
        procurement.pay_via_connector after a valid supplier_payment approval."""
        paid = []
        for oid in [x.strip() for x in (reference or "").split(",") if x.strip()]:
            data = await self._call("aliexpress.ds.order.afterpay", {"req": json.dumps({"order_id": oid})})
            if data.get("_error"):
                return OrderOutcome(False, error_message=data["_error"], evidence={"paid": paid})
            if data.get("error_response"):
                er = data["error_response"]
                return OrderOutcome(False, error_message=f"afterpay refused for {oid}: {er.get('code')}",
                                    evidence={"paid": paid})
            body = data.get("aliexpress_ds_order_afterpay_response") or {}
            if not _true(body.get("result")):
                if paid:      # some paid, this one not: never leave it unknown
                    raise AmbiguousSubmission(f"afterpay partially done ({paid}); {oid} not confirmed")
                return OrderOutcome(False, error_message=f"afterpay not successful for {oid}: {body.get('rsp_code')} "
                                                         f"{str(body.get('rsp_msg'))[:120]}")
            paid.append(oid)
        return OrderOutcome(True, order_id=reference, evidence={"connector": self.name, "afterpay": paid})

    async def get_status(self, reference: str) -> Dict[str, Any]:
        first = (reference or "").split(",")[0].strip()
        if not first.isdigit():
            return {"found": False, "status": None, "tracking": None, "reason": "no AliExpress order number"}
        data = await self._call("aliexpress.trade.ds.order.get", {"single_order_query": json.dumps({"order_id": first})})
        if data.get("_error") or data.get("error_response"):
            return {"found": False, "status": None, "tracking": None,
                    "reason": data.get("_error") or str(data["error_response"].get("code"))}
        res = self._result(data, "aliexpress.trade.ds.order.get") or {}
        tracking = None
        tr = await self._call("aliexpress.ds.order.tracking.get", {"ae_order_id": first, "language": "en_US"})
        lines = ((((self._result(tr, "aliexpress.ds.order.tracking.get") or {}).get("data")) or {})
                 .get("tracking_detail_line_list")) or []
        tracking = next((str(x.get("mail_no")) for x in lines if isinstance(x, dict) and x.get("mail_no")), None)
        return {"found": bool(res), "status": res.get("order_status"), "tracking": tracking}


# ── purchase order by email ─────────────────────────────────────────────────────────────────────────────
PO_TEMPLATE_VERSION = "po-email-v1"


def render_purchase_order(row: Dict[str, Any], offers: Dict[str, Dict[str, Any]], ship_to: Dict[str, Any]) -> Dict[str, str]:
    """Fixed purchase-order text built ONLY from the approved terms. No free text, no model output."""
    lines = []
    for it in row["items"]:
        o = offers.get(it["supplier_part_id"]) or {}
        lines.append(f"- Your SKU {o.get('supplier_sku') or '?'} x {int(it['quantity'])} at {it['unit_cost']} {it['currency']} each")
    ref = f"ASF-PO-{row['id'][:8].upper()}"
    ship = (row.get("terms") or {}).get("shipping") or {}
    cost = ship.get("cost")
    if cost in (None, "None", ""):
        ship_line = ("Shipping: no shipping charge has been approved for this order. If shipping is charged, please "
                     "do not process the order - reply with a shipping quote instead.")
    else:
        ship_line = f"Shipping: {cost} {ship.get('currency') or 'ILS'} (approved; please do not process if it differs)."
    body = (f"Hello,\n\nAutoSpareFinder would like to order the following items (purchase order {ref}, approval "
            f"reference {str(row.get('terms_hash') or '')[:12]}):\n\n"
            + "\n".join(lines)
            + f"\n\n{ship_line}\nPlease ship directly to our customer (dropshipping):"
            + f"\n{ship_to.get('full_name')}\n{ship_to.get('street')}\n{ship_to.get('city')} "
              f"{ship_to.get('postal_code') or ''}\n{ship_to.get('country')}\n\n"
              "This purchase order covers only the items, quantities and prices listed above. Please reply to confirm "
              "with your order number, the total amount, the shipping cost and the expected delivery date. If any "
              "price, quantity or availability differs, please do not process the order - reply with the change "
              "instead.\n\nThank you,\nAutoSpareFinder\nhttps://autosparefinder.co.il\n")
    return {"subject": f"Purchase order {ref} - AutoSpareFinder", "body": body, "reference": ref}


class EmailPurchaseOrderConnector(OrderConnector):
    mode = "email"
    name = "email_po"

    def __init__(self, recipient: str, client=None, sender: Optional[str] = None, address_loader=None,
                 offer_loader=None):
        self.recipient = recipient
        self._client = client
        self._sender = sender
        self._address = address_loader
        self._offer = offer_loader

    def _gmail(self):
        if self._client is None:
            from email_agent.config import load_config
            from email_agent.gmail_client import GmailClient
            cfg = load_config()
            self._client, self._sender = GmailClient(cfg), self._sender or cfg.mailbox
        return self._client

    async def place_order(self, row: Dict[str, Any]) -> OrderOutcome:
        from email_agent.gmail_client import PurchaseOrderEmailAuthorization
        from supplier_outreach import _draft_matches, build_raw
        try:
            ship_to = await self._address(row["order_id"])
            offers = {it["supplier_part_id"]: (await self._offer(it["supplier_part_id"])) or {} for it in row["items"]}
        except Exception as e:
            return OrderOutcome(False, error_message=f"could not prepare the purchase order: {type(e).__name__}")
        po = render_purchase_order(row, offers, ship_to or {})
        client = self._gmail()
        try:
            created = await asyncio.to_thread(client.create_new_draft, build_raw(self._sender, self.recipient,
                                                                                  po["subject"], po["body"]))
            draft_id = str(created["id"])
            back = await asyncio.to_thread(client.get_draft, draft_id)
        except Exception as e:                       # nothing sent yet: definite failure
            return OrderOutcome(False, error_message=f"draft failed: {type(e).__name__}")
        if not _draft_matches(back, self.recipient, po["body"]):
            return OrderOutcome(False, error_message="draft read-back did not match the purchase order - not sent")
        auth = PurchaseOrderEmailAuthorization(procurement_id=row["id"], draft_id=draft_id,
                                               authorization_id=str(row.get("po_authorization_id") or ""),
                                               recipient=self.recipient,
                                               body_sha256=hashlib.sha256(po["body"].encode()).hexdigest())
        try:
            res = await asyncio.to_thread(client.send_purchase_order_email, auth)
        except Exception as e:
            raise AmbiguousSubmission(f"purchase-order email send outcome unknown: {type(e).__name__}") from None
        return OrderOutcome(True, order_id=f"email:{res.get('id')}",
                            evidence={"connector": self.name, "recipient": self.recipient, "gmail_message_id": res.get("id"),
                                      "gmail_thread_id": res.get("threadId"), "po_reference": po["reference"],
                                      "awaiting_supplier_confirmation": True})


# ── selection ───────────────────────────────────────────────────────────────────────────────────────────
# onboarding statuses seen live 2026-10-27 (ASAP / Keystone / Turn 14 / Meyer / ATD) - none of them is orderable
ONBOARDING_STATUSES = frozenset({"pending_credentials", "pending_review", "application_submitted_pending_review",
                                 "account_active_pending_brand_approval"})


def connector_for(supplier: Dict[str, Any], **deps) -> OrderConnector:  # noqa: C901
    """supplier: {"id", "name", "is_active", "credentials"}. Order of rules: onboarding / inactive suppliers are never
    orderable; then the master switch; then the supplier's own connection."""
    creds = supplier.get("credentials") or {}
    if isinstance(creds, str):
        try:
            creds = json.loads(creds)
        except ValueError:
            creds = {}
    if creds.get("status") in ONBOARDING_STATUSES or (creds.get("needs") and supplier.get("is_active") is not True):
        return AwaitingCredentialsConnector(str(creds.get("needs") or creds.get("status") or ""))
    if supplier.get("is_active") is not True:
        return ManualConnector("supplier is not active - it cannot be ordered from (activation is the owner's decision)")
    if not auto_order_enabled():
        return ManualConnector("automatic ordering is switched off (SUPPLIER_AUTO_ORDER_ENABLED=0)")
    name = str(supplier.get("name") or "").lower()
    if "aliexpress" in name:
        return AliExpressDsConnector(**{k: v for k, v in deps.items() if k in ("supplier", "http_post",
                                                                                 "address_loader", "offer_loader",
                                                                                 "usd_rate")})
    if creds.get("ordering") == "email":
        import supplier_qualification as _q
        q = creds.get("qualification") or {}
        orders = [c for c in (_q.verified_contacts(q) if q else []) if c.get("kind") in ("orders", "sales")]
        if not q or not _q.classify(q)["dropshipping_ready"]:
            # an emailed purchase order ships straight to OUR customer: only a DROPSHIPPING-READY supplier qualifies
            return ManualConnector("supplier is not DROPSHIPPING-READY (direct-to-customer / Israel delivery not verified)")
        if orders:
            return EmailPurchaseOrderConnector(orders[0]["address"], **{k: v for k, v in deps.items() if k in (
                "client", "sender", "address_loader", "offer_loader")})
        return ManualConnector("email ordering configured but no VERIFIED orders/sales contact")
    return ManualConnector()


# ── production loaders ──────────────────────────────────────────────────────────────────────────────────
async def load_shipping_address(order_id: str) -> Dict[str, Any]:
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import pii_session_factory
    async with pii_session_factory() as db:
        v = (await db.execute(text("SELECT shipping_address FROM orders WHERE id = CAST(:i AS uuid)"),
                              {"i": order_id})).scalar()
    v = json.loads(v) if isinstance(v, str) else (v or {})
    return v


async def load_offer(supplier_part_id: str) -> Dict[str, Any]:
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import async_session_factory
    async with async_session_factory() as db:
        r = (await db.execute(text("SELECT supplier_url, price_usd, supplier_sku FROM supplier_parts "
                                   "WHERE id = CAST(:i AS uuid)"), {"i": supplier_part_id})).first()
    return {"supplier_url": r.supplier_url, "price_usd": float(r.price_usd or 0), "supplier_sku": r.supplier_sku} if r else {}


async def load_supplier(supplier_id: str) -> Dict[str, Any]:
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import async_session_factory
    async with async_session_factory() as db:
        r = (await db.execute(text("SELECT id::text AS id, name, is_active, credentials FROM suppliers "
                                   "WHERE id = CAST(:i AS uuid)"), {"i": supplier_id})).first()
    return dict(r._mapping) if r else {"id": supplier_id, "name": "", "is_active": False, "credentials": {}}


async def production_connector(supplier_id: str) -> OrderConnector:
    async def usd_rate():
        from BACKEND_DATABASE_MODELS import async_session_factory
        from currency_rate import get_usd_to_ils_rate
        async with async_session_factory() as db:
            return float(await get_usd_to_ils_rate(db))

    return connector_for(await load_supplier(supplier_id), address_loader=load_shipping_address,
                         offer_loader=load_offer, usd_rate=usd_rate)
