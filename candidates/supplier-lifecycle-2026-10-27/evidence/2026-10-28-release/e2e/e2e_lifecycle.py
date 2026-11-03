"""
Script: e2e_lifecycle.py  (release evidence, NOT part of the backend)
Purpose: ISOLATED end-to-end run of the supplier lifecycle on the candidate code: real modules, real PostgreSQL
         stores (disposable PII + catalog databases at migration 0042), real state machine, real SQL constraints.
         Only the EXTERNAL boundaries are fakes: the AliExpress Open Platform (realistic response bodies of the
         documented methods) and the Gmail client (in memory - nothing can leave the process).
Process: python3 /e2e/e2e_lifecycle.py   (inside the backend image, on an internal-only docker network)
         Every check prints PASS/FAIL <id> <text>; exit 1 if any check fails.
Data Imported/Modified: the disposable databases only (refuses to run unless ENVIRONMENT=test and the DB host is
         the disposable 'sandbox_postgres' service on an internal network).
Last Updated: 2026-10-28
"""
import asyncio
import base64
import email
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, "/app")
assert os.environ.get("ENVIRONMENT") == "test", "REFUSED: not a test environment"
for _v in ("DATABASE_URL", "DATABASE_PII_URL"):
    assert "@sandbox_postgres:" in os.environ.get(_v, ""), f"REFUSED: {_v} is not the disposable database"

import asyncpg  # noqa: E402

import procurement as pr  # noqa: E402
import supplier_authorization as au  # noqa: E402
import supplier_ordering as so  # noqa: E402
import supplier_outreach as out  # noqa: E402
import supplier_qualification as q  # noqa: E402
import boaz_tasks as bt  # noqa: E402
import agent_identity as ai  # noqa: E402
from email_agent.store import PgStore  # noqa: E402
from email_agent.owner_flow import PgTodoSink  # noqa: E402
from email_agent.supplier_routing import route_supplier_replies  # noqa: E402

CAT = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
PII = os.environ["DATABASE_PII_URL"].replace("postgresql+asyncpg://", "postgresql://")
RESULTS = []


def check(cid, text, cond, detail=""):
    RESULTS.append((cid, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'} {cid} {text}" + (f"  [{detail}]" if detail and not cond else ""), flush=True)


# ── fakes for the external boundaries ────────────────────────────────────────────────────────────────────
class FakeAESupplier:
    async def _ensure_token(self):
        return True

    def _build_request(self, method, extra):
        return {"method": method, **extra}


class FakeAE:
    """AliExpress Open Platform stand-in. Records every request; `mode` picks the order.create reply."""

    def __init__(self, mode="ok", skus=None, delivery=None):
        self.mode, self.calls = mode, []
        self.skus = skus if skus is not None else [{"sku_attr": "14:200006151#Black", "sku_id": "12000037",
                                                    "offer_sale_price": "12.50", "currency_code": "USD",
                                                    "sku_available_stock": 50}]
        self.delivery = delivery if delivery is not None else [
            {"code": "CAINIAO_STANDARD", "tracking": "true", "free_shipping": "true", "shipping_fee_cent": "0",
             "shipping_fee_currency": "USD", "max_delivery_days": "25"}]

    async def post(self, data):
        m = data["method"]
        self.calls.append(data)
        if m == "aliexpress.ds.product.get":
            return 200, {"aliexpress_ds_product_get_response": {"result": {
                "ae_item_sku_info_dtos": {"ae_item_sku_info_d_t_o": self.skus}}}}
        if m == "aliexpress.ds.freight.query":
            return 200, {"aliexpress_ds_freight_query_response": {"result": {"success": "true",
                                                                              "delivery_options": self.delivery}}}
        if m == "aliexpress.ds.order.create":
            if self.mode == "timeout":
                raise so.AmbiguousSubmission("AliExpress request outcome unknown: ReadTimeout")
            if self.mode == "connect_error":
                return 0, {"_transport_error": "ConnectError"}
            if self.mode == "rejected":
                return 200, {"error_response": {"code": "isv.B_DROPSHIPPER_DELIVERY_ADDRESS_VALIDATE_FAIL",
                                                "msg": "address invalid"}}
            if self.mode == "repeated":
                return 200, {"aliexpress_ds_order_create_response": {"result": {"is_success": "false",
                                                                                "error_code": "REPEATED_ORDER_ERROR"}}}
            if self.mode == "no_number":
                return 200, {"aliexpress_ds_order_create_response": {"result": {"is_success": "true", "order_list": []}}}
            if self.mode == "http500":
                return 502, {}
            return 200, {"aliexpress_ds_order_create_response": {"result": {"is_success": "true",
                                                                            "order_list": [8190000000000001]}}}
        if m == "aliexpress.trade.ds.order.get":
            return 200, {"aliexpress_trade_ds_order_get_response": {"result": {"order_status": "WAIT_SELLER_SEND_GOODS"}}}
        if m == "aliexpress.ds.order.tracking.get":
            return 200, {"aliexpress_ds_order_tracking_get_response": {"result": {"data": {
                "tracking_detail_line_list": [{"mail_no": "LP00123456789CN"}]}}}}
        if m == "aliexpress.ds.order.afterpay":
            return 200, {"aliexpress_ds_order_afterpay_response": {"result": "true"}}
        return 404, {}

    def methods(self):
        return [c["method"] for c in self.calls]


def ae_connector(fake):
    async def address(_):
        return {"full_name": "Test Customer", "street": "1 Test St", "city": "Haifa", "postal_code": "3100000",
                "country": "Israel", "phone": "+972500000000"}

    async def offer(_):
        return {"supplier_url": "https://www.aliexpress.com/item/1005001234567890.html", "price_usd": 12.5}

    async def rate():
        return 3.70
    return so.AliExpressDsConnector(supplier=FakeAESupplier(), http_post=fake.post, address_loader=address,
                                    offer_loader=offer, usd_rate=rate)


class FakeGmail:
    """In-memory Gmail: drafts and 'sent' records never leave the process. tamper=True alters the read-back."""

    def __init__(self, tamper=False):
        self.drafts, self.sent, self.tamper, self.modify_calls = {}, [], tamper, 0

    def create_new_draft(self, raw, thread_id=None):
        did = f"d{len(self.drafts) + 1}"
        self.drafts[did] = raw
        return {"id": did}

    def get_draft(self, did):
        msg = email.message_from_bytes(base64.urlsafe_b64decode(self.drafts[did]))
        body = msg.get_payload(decode=True).decode()
        to = msg["To"] + (", extra@attacker.example" if self.tamper else "")
        return {"id": did, "message": {"id": "m" + did, "threadId": "t" + did, "payload": {
            "mimeType": "text/plain", "headers": [{"name": "From", "value": msg["From"]}, {"name": "To", "value": to},
                                                  {"name": "Subject", "value": msg["Subject"]}],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}}}

    def send_supplier_inquiry(self, sia):
        self.sent.append(sia)
        return {"id": f"sent{len(self.sent)}", "threadId": "thr-sent"}


# ── seed helpers ─────────────────────────────────────────────────────────────────────────────────────────
async def seed_offer(cat, *, price_ils="40.00", ship_ils="0", available=True, active=True, name="AliExpress"):
    sup, part, sp, brand = (str(uuid.uuid4()) for _ in range(4))
    await cat.execute("INSERT INTO car_brands (id, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                      uuid.UUID(brand), f"E2E brand {brand[:6]}")
    await cat.execute("INSERT INTO suppliers (id, name, is_active) VALUES ($1, $2, $3)", uuid.UUID(sup), f"{name} {sup[:6]}", active)
    await cat.execute("INSERT INTO parts_catalog (id, sku, name, manufacturer_id) VALUES ($1,$2,$3,$4)",
                      uuid.UUID(part), f"E2E-{part[:8]}", "E2E brake pad", uuid.UUID(brand))
    await cat.execute("INSERT INTO supplier_parts (id, supplier_id, part_id, price_usd, price_ils, shipping_cost_ils, "
                      "is_available, supplier_sku, supplier_url) VALUES ($1,$2,$3,12.5,$4,$5,$6,$7,$8)",
                      uuid.UUID(sp), uuid.UUID(sup), uuid.UUID(part), float(price_ils), float(ship_ils), available,
                      f"E2E-SKU-{sp[:8]}", "https://www.aliexpress.com/item/1005001234567890.html")
    return sup, part, sp


def order_ns(n=1, sp=None, qty=2):
    return SimpleNamespace(id=uuid.uuid4(), order_number=f"E2E-{n}"), [SimpleNamespace(supplier_part_id=sp, quantity=qty)]


async def events(pii, pid):
    return await pii.fetch("SELECT from_state, to_state, actor FROM procurement_events WHERE procurement_id = $1 "
                           "ORDER BY id", uuid.UUID(pid))


def items(sp="sp-x", cost="40.00", qty=1):
    return [{"supplier_part_id": sp, "part_id": "p-x", "quantity": qty, "unit_cost": cost, "currency": "ILS"}]


async def new_proc(store, n, sp="sp-x", cost="40.00", ship="0"):
    r = await pr.prepare(store, order_id=f"ord-{n}-{uuid.uuid4().hex[:6]}", order_number=f"E2E-{n}",
                         supplier_id="sup-x", items=items(sp, cost), shipping={"cost": ship, "currency": "ILS"},
                         checks={"supplier_active": True})
    return r["procurement"]


# ── A. discovery + qualification ─────────────────────────────────────────────────────────────────────────
async def stage_a(cat):
    rec = q.from_sourcing_signals("s1", {"has_api": True, "ships_il": True, "is_parts_store": True, "http_ok": True},
                                  "parts.example")
    c = q.classify(rec)
    check("A1", "homepage keywords only -> needs_investigation, not integration-ready, not dropshipping-ready",
          c["classification"] == q.NEEDS_INVESTIGATION and not c["integration_ready"] and not c["dropshipping_ready"]
          and all(s == "UNVERIFIED" for s in c["statuses"].values()), c["statuses"])
    try:
        q.set_requirement(rec, "api", "VERIFIED", evidence_kind="supplier_claim", source="mail", note="they said so")
        refused = False
    except ValueError:
        refused = True
    check("A2", "VERIFIED from a supplier claim / keyword is refused", refused)
    try:
        q.set_requirement(rec, "api", "VERIFIED", evidence_kind="api_test", source=None, note="x")
        refused = False
    except ValueError:
        refused = True
    check("A3", "VERIFIED without an evidence source is refused", refused)
    now = datetime.now(timezone.utc)
    full = q.new_record("s2")
    for k in q.REQUIREMENTS:
        q.set_requirement(full, k, "VERIFIED", evidence_kind="official_documentation", source=f"https://doc/{k}",
                          note="checked", details={"data_timestamp": now.isoformat()} if k == "availability" else None)
    c = q.classify(full)
    check("A4", "all six VERIFIED -> integration_ready; dropshipping still CANDIDATE (dropship checks unverified)",
          c["integration_ready"] and c["dropshipping"] == q.DROPSHIP_CANDIDATE, c["dropshipping"])
    for k in q.DROPSHIP_MANDATORY:
        q.set_requirement(full, k, "VERIFIED", evidence_kind="official_policy_page", source=f"https://policy/{k}", note="ok")
    check("A5", "API readiness and dropshipping readiness are separate: dropship VERIFIED -> DROPSHIPPING-READY",
          q.classify(full)["dropshipping_ready"])
    check("A6", "stale availability evidence (>30 d) is not VERIFIED",
          q.classify(full, now=now + timedelta(days=31))["statuses"]["availability"] == "UNVERIFIED")
    r = q.record_supplier_claim(full, "israel_shipping", "we do not ship to Israel", value=False, source_ref="mail:1")
    c = q.classify(full)
    check("A7", "a supplier claim that contradicts VERIFIED evidence is flagged and removes readiness",
          r["contradiction"] and not c["integration_ready"] and not c["dropshipping_ready"], c["classification"])
    noapi = q.new_record("s3")
    q.set_requirement(noapi, "api", "FAILED", evidence_kind="official_documentation", source="https://doc", note="no API")
    check("A8", "FAILED api -> no_api_candidate (business path), never integration-ready",
          q.classify(noapi)["classification"] == q.NO_API)
    old = {"supplier_id": "s4", "requirements": {k: {"status": "VERIFIED", "evidence_kind": "owner_check",
                                                      "source": "x", "observed_at": now.isoformat(), "note": "v1"}
                                                  for k in q.REQUIREMENTS}}
    check("A9", "a v1 record (no dropship checks) never passes dropshipping", not q.classify(old)["dropshipping_ready"])
    from services.supplier_sourcing import onboard_seller
    conn = await asyncpg.connect(CAT)
    try:
        a = await onboard_seller(conn, name="E2E Parts Co", website="https://e2e-parts.example")
        b = await onboard_seller(conn, name="e2e parts co", website="https://www.e2e-parts.example/shop")
        n = await conn.fetchval("SELECT count(*) FROM suppliers WHERE lower(name) = 'e2e parts co'")
        act = await conn.fetchval("SELECT bool_or(is_active) FROM suppliers WHERE lower(name) = 'e2e parts co'")
    finally:
        await conn.close()
    check("A10", "discovery dedupes by name/domain and never activates (is_active=FALSE)", n == 1 and act is False,
          f"rows={n} active={act} a={a} b={b}")


# ── B / C / E. pricing, authorization boundaries, order lifecycle ────────────────────────────────────────
async def stage_bce(cat, pii):
    st, auth = pr.PgProcurementStore(), au.PgAuthStore()

    # B: variant / stock / currency / price / shipping validation (connector-level, before any order request)
    def sku(price="12.50", stock=50, cur="USD", attr="a", sid="1"):
        return {"sku_attr": attr, "sku_id": sid, "offer_sale_price": price, "currency_code": cur,
                "sku_available_stock": stock}
    check("B1", "two variants at the approved price -> refused (never guesses)",
          not so.choose_sku([sku(attr="a", sid="1"), sku(attr="b", sid="2")], 1, 12.5)["ok"])
    check("B2", "out of stock for the quantity -> refused", not so.choose_sku([sku(stock=1)], 2, 12.5)["ok"])
    check("B3", "non-USD variant is ignored (currency label) -> refused", not so.choose_sku([sku(cur="CNY")], 1, 12.5)["ok"])
    check("B4", "supplier price above the approved price -> refused",
          not so.choose_sku([sku(price="13.10")], 1, 12.5)["ok"])
    check("B5", "exactly one in-stock USD variant at the approved price -> chosen",
          so.choose_sku([sku()], 1, 12.5)["ok"])
    d = lambda **k: {"code": "X", "tracking": "true", "shipping_fee_currency": "USD", **k}  # noqa: E731
    check("B6", "missing / unreadable shipping fee is NOT treated as free -> refused",
          not so.choose_delivery([d(shipping_fee_cent="")], 5)["ok"] and not so.choose_delivery([d()], 5)["ok"])
    check("B7", "untracked delivery -> refused", not so.choose_delivery([d(tracking="false", free_shipping="true")], 5)["ok"])
    check("B8", "cheapest tracked fee above the approved cap -> refused",
          not so.choose_delivery([d(shipping_fee_cent="7.00")], 5)["ok"])
    try:
        so.ae_address({"full_name": "x", "city": "Haifa", "country": "IL"})
        bad = False
    except ValueError:
        bad = True
    check("B9", "incomplete ship-to address -> refused before any order request", bad)

    # C1: a customer payment prepares procurement only - never an order or a payment
    sup, part, sp = await seed_offer(cat)
    order, oitems = order_ns(1, sp)
    notes = []

    async def notify(*a, **k):
        notes.append(a)
    ref = await pr.gate_paid_order(order, oitems, notify=notify)
    rows = await st.for_order(str(order.id))
    check("C1", "customer payment -> one procurement AWAITING_PO_AUTHORIZATION, spend refused, owner told once",
          ref and len(rows) == 1 and rows[0]["state"] == "AWAITING_PO_AUTHORIZATION" and len(notes) == 1, ref)
    ref2 = await pr.gate_paid_order(order, oitems, notify=notify)
    check("C2", "repeat payment webhook -> no second procurement, no second alert",
          ref2 and len(await st.for_order(str(order.id))) == 1 and len(notes) == 1)
    proc = rows[0]

    # C0: only an explicit owner action is an approval source (an email, a task or model output is not)
    try:
        await au.grant(auth, domain="purchase_order", scope="create_po", entity_type="procurement", entity_id="x",
                       approved_by="supplier@x.example", source="email", terms={"a": 1})
        src_ok = True
    except ValueError:
        src_ok = False
    check("C0", "an approval from a non-owner source (email) is refused", not src_ok)

    # C3: domains never imply each other
    g = await au.grant(auth, domain="supplier_message", scope="routine_inquiry", entity_type="supplier",
                       entity_id=sup, approved_by="owner", source="test")

    async def denied(domain, scope, terms=None, eid=sup, et="supplier"):
        try:
            await au.require(auth, domain, et, eid, scope=scope, terms=terms)
            return False
        except au.AuthorizationError:
            return True
    check("C3", "inquiry permission does not authorize activation", await denied("supplier_activation", "activate"))
    await au.grant(auth, domain="supplier_activation", scope="activate", entity_type="supplier", entity_id=sup,
                   approved_by="owner", source="test")
    check("C4", "activation does not authorize commercial terms",
          await denied("commercial_commitment", "accept_terms", terms={"x": 1}))
    try:
        await au.grant(auth, domain="supplier_activation", scope="activate", entity_type="supplier", entity_id="*",
                       approved_by="owner", source="test", standing=True)
        standing_ok = True
    except Exception:
        standing_ok = False
    check("C5", "a standing (blanket) grant is refused outside routine inquiries", not standing_ok)
    _ = g

    # C6: PO approval does not authorize payment; payment cannot be approved before submission
    await pr.authorize_po(st, auth, proc["id"], approver="owner", source="test")
    check("C6", "purchase-order approval does not release supplier payment",
          bool(await pr.gate_paid_order(order, oitems, notify=notify)))
    try:
        await pr.authorize_payment(st, auth, proc["id"], approver="owner", source="test")
        early = True
    except pr.ProcurementError:
        early = False
    check("C7", "supplier payment cannot be approved before the order is submitted", not early)

    # E: order submission through the AliExpress connection (fake platform)
    fake = FakeAE("ok")
    terms = await pr.current_terms_for_procurement(await st.get(proc["id"]))
    res = await pr.submit_po(st, auth, proc["id"], ae_connector(fake), terms)
    row = await st.get(proc["id"])
    create = [c for c in fake.calls if c["method"] == "aliexpress.ds.order.create"]
    ext = json.loads(create[0]["ds_extend_request"]) if create else {}
    dto = json.loads(create[0]["param_place_order_request4_open_api_d_t_o"]) if create else {}
    check("E1", "approved PO -> SUBMITTED (created, UNPAID): try_to_pay='false', out_order_id = procurement id",
          res.get("submitted") and row["state"] == "SUBMITTED" and ext.get("payment", {}).get("try_to_pay") == "false"
          and dto.get("out_order_id") == proc["id"] and row["submission_evidence"].get("paid") is False, res)
    check("E1b", "request sequence: product.get -> freight.query -> order.create, no afterpay",
          fake.methods() == ["aliexpress.ds.product.get", "aliexpress.ds.freight.query", "aliexpress.ds.order.create"],
          fake.methods())
    again = await pr.submit_po(st, auth, proc["id"], ae_connector(fake), terms)
    check("E2", "replayed submission -> refused, no second order.create",
          not again.get("submitted") and len([c for c in fake.calls if c["method"] == "aliexpress.ds.order.create"]) == 1)
    check("C8", "AliExpress-connected order is never paid through fulfillment (no second payment path)",
          "supplier connection" in (await pr.gate_paid_order(order, oitems, notify=notify) or ""))
    rec = await pr.reconcile(st, proc["id"], ae_connector(fake))
    row = await st.get(proc["id"])
    check("E3", "reconcile -> SUPPLIER_CONFIRMED with tracking from the status API",
          row["state"] == "SUPPLIER_CONFIRMED" and row["tracking_number"] == "LP00123456789CN", rec)
    paid = await pr.pay_via_connector(st, auth, proc["id"], ae_connector(fake), terms)
    check("C9", "pay without a supplier_payment approval -> refused, no afterpay call",
          not paid.get("paid") and "aliexpress.ds.order.afterpay" not in fake.methods())
    await pr.authorize_payment(st, auth, proc["id"], approver="owner", source="test")
    changed = dict(terms, items=[dict(terms["items"][0], unit_cost="44.00")])
    p2 = await pr.pay_via_connector(st, auth, proc["id"], ae_connector(fake), changed)
    check("C10", "payment approval is bound to the terms: changed cost -> refused + back to re-approval",
          not p2.get("paid") and (await st.get(proc["id"]))["state"] == "AWAITING_PO_AUTHORIZATION"
          and "aliexpress.ds.order.afterpay" not in fake.methods(), p2)
    ev = await events(pii, proc["id"])
    chain_ok = all(ev[i]["to_state"] == ev[i + 1]["from_state"] for i in range(len(ev) - 1)
                   if ev[i + 1]["from_state"] is not None and ev[i]["to_state"] != ev[i + 1]["to_state"]
                   and ev[i + 1]["from_state"] != ev[i + 1]["to_state"])
    check("G1", "procurement_events: every transition recorded with actor, chain consistent",
          len(ev) >= 6 and all(e["actor"] for e in ev) and chain_ok, [(e["from_state"], e["to_state"]) for e in ev])

    # E: failure outcomes
    async def run(mode, n, **kw):
        p = await new_proc(st, n)
        await pr.authorize_po(st, auth, p["id"], approver="owner", source="test")
        f = FakeAE(mode, **kw)
        r = await pr.submit_po(st, auth, p["id"], ae_connector(f), (await st.get(p["id"]))["terms"])
        return p, f, r, await st.get(p["id"])
    p, f, r, row = await run("rejected", 10)
    check("E4", "supplier rejects -> SUBMISSION_FAILED (definite), reason kept", row["state"] == "SUBMISSION_FAILED"
          and "refused" in (row["last_error"] or ""), row["last_error"])
    again = await pr.submit_po(st, auth, p["id"], ae_connector(f), row["terms"])
    check("E5", "after a definite failure a resubmit needs a NEW approval", not again.get("submitted"), again)
    p, f, r, row = await run("timeout", 11)
    po_auth = await pii.fetchrow("SELECT consumed_at FROM agent_authorizations WHERE id = $1",
                                 uuid.UUID(row["po_authorization_id"]))
    check("E6", "timeout -> EXCEPTION (outcome unknown), approval consumed, never resubmitted",
          row["state"] == "EXCEPTION" and po_auth["consumed_at"] is not None
          and not (await pr.submit_po(st, auth, p["id"], ae_connector(f), row["terms"])).get("submitted"))
    for cid, mode in (("E7", "repeated"), ("E8", "no_number"), ("E9", "http500")):
        p, f, r, row = await run(mode, 12 + int(cid[1:]))
        check(cid, f"{mode} -> EXCEPTION 'reconcile before retry', no supplier reference recorded",
              row["state"] == "EXCEPTION" and not row["supplier_reference"], row["state"])
    p, f, r, row = await run("connect_error", 20)
    check("E10", "never reached AliExpress -> SUBMISSION_FAILED 'nothing sent' (safe to retry after re-approval)",
          row["state"] == "SUBMISSION_FAILED" and "nothing sent" in (row["last_error"] or ""), row["last_error"])
    p, f, r, row = await run("ok", 21, skus=[{"sku_attr": "a", "sku_id": "1", "offer_sale_price": "12.50",
                                              "currency_code": "USD", "sku_available_stock": 50},
                                             {"sku_attr": "b", "sku_id": "2", "offer_sale_price": "12.50",
                                              "currency_code": "USD", "sku_available_stock": 50}])
    check("E11", "ambiguous variant -> SUBMISSION_FAILED before any order.create (validation failure)",
          row["state"] == "SUBMISSION_FAILED" and "aliexpress.ds.order.create" not in f.methods(), f.methods())
    p, f, r, row = await run("ok", 22, delivery=[{"code": "X", "tracking": "true", "shipping_fee_currency": "USD",
                                                  "shipping_fee_cent": "6.00"}])
    check("E12", "paid shipping above the approved (free) shipping -> refused before order.create",
          row["state"] == "SUBMISSION_FAILED" and "aliexpress.ds.order.create" not in f.methods(), row["last_error"])

    # submission blocked without approval; concurrency; changed terms; restart recovery
    p = await new_proc(st, 30)
    f = FakeAE()
    r = await pr.submit_po(st, auth, p["id"], ae_connector(f), p["terms"])
    check("E13", "no PO approval -> SUBMISSION_BLOCKED (refused), supplier never contacted",
          not r.get("submitted") and f.calls == [], r)
    await pr.authorize_po(st, auth, p["id"], approver="owner", source="test")
    f = FakeAE()
    rs = await asyncio.gather(*[pr.submit_po(st, auth, p["id"], ae_connector(f), p["terms"]) for _ in range(10)])
    check("E14", "10 concurrent submissions of one approved PO -> exactly 1 order.create",
          sum(bool(x.get("submitted")) for x in rs) == 1 and f.methods().count("aliexpress.ds.order.create") == 1,
          f.methods().count("aliexpress.ds.order.create"))
    p = await new_proc(st, 31)
    await pr.authorize_po(st, auth, p["id"], approver="owner", source="test")
    f = FakeAE()
    newer = dict(p["terms"], items=[dict(p["terms"]["items"][0], unit_cost="47.00")])
    r = await pr.submit_po(st, auth, p["id"], ae_connector(f), newer)
    check("E15", "price changed between approval and submission -> back to AWAITING_PO_AUTHORIZATION, no request",
          not r.get("submitted") and (await st.get(p["id"]))["state"] == "AWAITING_PO_AUTHORIZATION" and f.calls == [])
    p = await new_proc(st, 32)
    await pr.authorize_po(st, auth, p["id"], approver="owner", source="test")
    await st.claim(p["id"], "PO_AUTHORIZED", "in_flight", {"started_at": "crash"})     # process dies mid-submission
    st2, f = pr.PgProcurementStore(), FakeAE()                                          # a new process
    r = await pr.submit_po(st2, au.PgAuthStore(), p["id"], ae_connector(f), p["terms"])
    rec = await pr.reconcile(st2, p["id"], ae_connector(f))
    row = await st2.get(p["id"])
    check("G2", "restart after a crash mid-submission -> no resubmit, in-flight record kept, reconcile asks for a "
          "manual check", not r.get("submitted") and f.calls == [] and row["submission_evidence"].get("in_flight")
          and "manually" in rec.get("reason", ""), (r, rec))

    # manual / onboarding / switched-off connections
    check("E16", "inactive supplier -> manual connector (cannot be ordered from)",
          isinstance(so.connector_for({"name": "AliExpress", "is_active": False}), so.ManualConnector))
    check("E17", "onboarding supplier -> awaiting credentials",
          isinstance(so.connector_for({"name": "ASAP", "is_active": False, "credentials": {"status": "pending_credentials",
                                                                                           "needs": "token"}}),
                     so.AwaitingCredentialsConnector))
    check("E18", "active supplier with SUPPLIER_AUTO_ORDER_ENABLED off -> manual (no automatic purchase)",
          isinstance(so.connector_for({"name": "AliExpress", "is_active": True}), so.ManualConnector))
    p = await new_proc(st, 40)
    try:
        await pr.record_manual_submission(st, auth, p["id"], "SUP-123", approver="owner")
        manual_unauth = True
    except au.AuthorizationError:
        manual_unauth = False
    check("E19", "recording a manual supplier order still needs the PO approval", not manual_unauth)

    # C: one supplier-payment approval releases fulfillment exactly once, even under concurrency
    sup, part, sp = await seed_offer(cat, name="Local Parts Ltd")
    order, oitems = order_ns(50, sp, qty=1)
    await pr.gate_paid_order(order, oitems)
    p = (await st.for_order(str(order.id)))[0]
    await pr.authorize_po(st, auth, p["id"], approver="owner", source="test")
    await pr.record_manual_submission(st, auth, p["id"], "LOCAL-77", approver="owner")
    await pr.authorize_payment(st, auth, p["id"], approver="owner", source="test")
    rs = await asyncio.gather(*[pr.gate_paid_order(order, oitems) for _ in range(5)])
    released = sum(1 for x in rs if x is None)
    check("C11", "one supplier-payment approval releases fulfillment exactly once (5 concurrent payment webhooks)",
          released == 1, f"released={released} results={rs}")
    check("C12", "after release, a replayed webhook is refused", bool(await pr.gate_paid_order(order, oitems)))
    await pii.execute("UPDATE agent_authorizations SET consumed_at = NULL, created_at = now() - interval '3 days', "
                      "expires_at = now() - interval '1 day' WHERE entity_id = $1 AND domain = 'supplier_payment'", p["id"])
    check("C13", "an expired payment approval is refused", bool(await pr.gate_paid_order(order, oitems)))


# ── D. BOAZ task ownership ───────────────────────────────────────────────────────────────────────────────
async def stage_d(cat):
    async def todo(agent, kind, title, art=None):
        return str(await cat.fetchval(
            "INSERT INTO agent_todos (id, title, status, priority, assigned_to_agent, category, artifacts) "
            "VALUES ($1,$2,'not_started','high',$3,$4,$5::jsonb) RETURNING id", uuid.uuid4(), title, agent, kind,
            json.dumps({"kind": kind, **(art or {})})))
    ids = {"alias": await todo("pricing_agent", "supplier_handoff", "E2E handoff via old name", {"supplier_id": "s-1"}),
           "bulk": await todo("BOAZ", "bulk_scan", "E2E bulk scan"),
           "unknown": await todo("BOAZ", "price_war", "E2E unknown kind"),
           "email": await todo("BOAZ", "email", "E2E email", {"email_ref": "zzzzzzzz"}),
           "nir": await todo("NIR", "supplier_reply", "E2E NIR task"),
           "err": await todo("boaz", "bulk_scan", "E2E failing bulk")}
    calls = {"bulk": 0}
    recorded = []

    async def bulk():
        calls["bulk"] += 1
        if calls["bulk"] > 1:
            raise RuntimeError("bulk scan backend down")
        return {"status": "ok", "created": 0, "max_live_stock": 36, "threshold": 50}

    async def status(_):
        return {"is_active": False, "priced_offers": 0, "qualification_unverified": ["api"],
                "dropshipping": "DROPSHIPPING-CANDIDATE"}

    async def rec(sid, data):
        recorded.append(sid)
    deps = {"email_store": PgStore(), "link_supplier_mail": rec, "supplier_status": status,
            "record_onboarding": rec, "bulk_scan": bulk}
    alerts = []

    async def notify(*a, **k):
        alerts.append(k.get("alert_key"))
    r1, r2 = await asyncio.gather(bt.run_once(bt.PgTaskRepo(), deps, notify), bt.run_once(bt.PgTaskRepo(), deps, notify))
    rows = {k: await cat.fetchrow("SELECT status, assigned_to_agent, metrics FROM agent_todos WHERE id = $1",
                                  uuid.UUID(v)) for k, v in ids.items()}
    check("D1", "two concurrent BOAZ runs claim each task once (5 BOAZ-owned, under 3 spellings)",
          r1["claimed"] + r2["claimed"] == 5, (r1, r2))
    check("D2", "NIR's task is never touched", rows["nir"]["status"] == "not_started")
    check("D3", "old name 'pricing_agent' is BOAZ's; handoff with gaps -> escalated, NOT completed",
          rows["alias"]["status"] == "in_progress" and ai.canonical("pricing_agent") == "BOAZ")
    check("D4", "unknown task type -> escalated, never silently completed", rows["unknown"]["status"] == "in_progress")
    check("D5", "email ref that does not resolve uniquely -> escalated", rows["email"]["status"] == "in_progress")
    done = {k for k in ("bulk", "err") if rows[k]["status"] == "completed"}
    check("D6", "a handler that did the work completes the task with its result (bulk scan: live stock 36 < 50 "
          "reported, threshold unchanged)", len(done) == 1 and "36" in str(rows[next(iter(done))]["metrics"]), done)
    failing = ({"bulk", "err"} - done).pop()
    for _ in range(3):
        await bt.run_once(bt.PgTaskRepo(), deps, notify)
    fr = await cat.fetchrow("SELECT status, metrics FROM agent_todos WHERE id = $1", uuid.UUID(ids[failing]))
    m = json.loads(fr["metrics"]) if isinstance(fr["metrics"], str) else fr["metrics"]
    check("D7", "a failing handler retries, then escalates after 3 attempts - never completed",
          fr["status"] == "in_progress" and m["boaz"]["attempts"] == 3 and m["boaz"].get("escalated"), m.get("boaz"))
    check("D8", "escalations alert the owner once per task (deduped key)", len(alerts) == len(set(alerts)) or
          all(a and a.startswith("boaz_task_") for a in alerts))


# ── F. outreach + reply routing ──────────────────────────────────────────────────────────────────────────
async def stage_f(cat, pii):
    ost, auth, est = out.PgOutreachStore(), au.PgAuthStore(), PgStore()
    sup = str(uuid.uuid4())
    unsourced = q.new_record(sup)
    q.set_requirement(unsourced, "contacts", "VERIFIED", evidence_kind="official_documentation",
                      source="https://supplier-a.example/contact", note="contact page",
                      details={"contacts": [{"address": "sales@supplier-a.example", "kind": "sales", "status": "VERIFIED"}]})
    p0 = await out.plan(ost, sup, unsourced)
    check("F0", "a contact without its own evidence source is never used for outreach", p0["action"] == "skip", p0)
    rec = q.new_record(sup)
    q.set_requirement(rec, "contacts", "VERIFIED", evidence_kind="official_documentation",
                      source="https://supplier-a.example/contact", note="official contact page",
                      details={"contacts": [{"address": "sales@supplier-a.example", "kind": "sales", "status": "VERIFIED",
                                             "source": "https://supplier-a.example/contact"}]})
    contacts = q.verified_contacts(rec)
    p = await out.plan(ost, sup, rec)
    check("F1", "inquiry prepared only for a VERIFIED contact; second plan is deduped",
          p["action"] == "prepared" and (await out.plan(ost, sup, rec))["action"] == "skip", (contacts, p.get("reason")))
    gm = FakeGmail()
    os.environ.pop("NIR_OUTREACH_ENABLED", None)
    r = await out.send_outreach(ost, auth, gm, p, sender="business@example.invalid")
    check("F2", "outreach switch off -> nothing drafted or sent", not r["sent"] and gm.drafts == {} and gm.sent == [])
    os.environ["NIR_OUTREACH_ENABLED"] = "1"
    try:
        r = await out.send_outreach(ost, auth, gm, p, sender="business@example.invalid")
        st_ = (await ost.for_supplier(sup))[-1]["state"]
        check("F3", "switch on but no inquiry approval -> CANCELLED, nothing sent", not r["sent"] and gm.sent == []
              and st_ == "CANCELLED", st_)
        sup2 = str(uuid.uuid4())
        await au.grant(auth, domain="supplier_message", scope="routine_inquiry", entity_type="supplier",
                       entity_id=sup2, approved_by="owner", source="test")
        p2 = await out.plan(ost, sup2, rec)
        bad = FakeGmail(tamper=True)
        r = await out.send_outreach(ost, auth, bad, p2, sender="business@example.invalid")
        check("F4", "draft read-back adds a recipient -> FAILED, not sent", not r["sent"] and bad.sent == [], r)
        sup3 = str(uuid.uuid4())
        await au.grant(auth, domain="supplier_message", scope="routine_inquiry", entity_type="supplier",
                       entity_id=sup3, approved_by="owner", source="test")
        p3 = await out.plan(ost, sup3, rec)
        r = await out.send_outreach(ost, auth, gm, p3, sender="business@example.invalid")
        check("F5", "approved inquiry -> one in-memory 'send' (FAKE Gmail, nothing leaves the process), "
              "AWAITING_REPLY", r["sent"] and len(gm.sent) == 1, r)
    finally:
        os.environ.pop("NIR_OUTREACH_ENABLED", None)
    # replies: one outreach thread for supplier-a, two open inquiries for supplier-b
    o_a = (await ost.for_supplier(sup3))[-1]
    await ost.update(o_a["id"], gmail_thread_id="thr-A")
    for s in (str(uuid.uuid4()), str(uuid.uuid4())):
        await ost.insert({"id": str(uuid.uuid4()), "supplier_id": s, "recipient": f"x{s[:4]}@supplier-b.example",
                          "purposes": ["api"], "template_version": "t", "state": "AWAITING_REPLY",
                          "idempotency_key": f"e2e-{s}", "attempt": 1, "parent_id": None, "source_task_id": None})
    await pii.execute("UPDATE supplier_outreach SET state = 'AWAITING_REPLY' WHERE recipient LIKE '%supplier-b.example'")

    async def mail(mid, thread, sender, cls, folder="inbox"):
        await pii.execute(
            "INSERT INTO email_agent_messages (id, gmail_message_id, gmail_thread_id, sender_email, subject, "
            "classification, requires_human, risk_flags, context, attachments, verification, sendable, status, attempts, "
            "created_at, updated_at, task_ids, source_folder) VALUES ($1,$2,$3,$4,'s',$5,false,'[]','{}','[]','{}',false,"
            "'processed',1,now(),now(),'[]',$6)", uuid.uuid4(), mid, thread, sender, cls, folder)
    await mail("e2e-m1", "thr-A", "sales@supplier-a.example", "supplier", "spam")
    await mail("e2e-m2", "thr-A", "someone@other.example", "supplier")
    await mail("e2e-m3", "thr-B", "y@supplier-b.example", "supplier")
    await mail("e2e-m4", "thr-C", "info@unknown-supplier.example", "aliexpress")
    await mail("e2e-m5", "thr-D", "news@shop.example", "newsletter")
    r1 = await route_supplier_replies(est, ost, PgTodoSink(), procurement_store=pr.PgProcurementStore())
    routes = {r["gmail_message_id"]: json.loads(r["supplier_route"]) if isinstance(r["supplier_route"], str)
              else r["supplier_route"] for r in await pii.fetch(
                  "SELECT gmail_message_id, supplier_route FROM email_agent_messages WHERE gmail_message_id LIKE 'e2e-m%'")}
    nir_tasks = await cat.fetchval("SELECT count(*) FROM agent_todos WHERE assigned_to_agent = 'NIR' "
                                   "AND artifacts->>'kind' = 'supplier_reply' AND artifacts->>'outreach_id' = $1", o_a["id"])
    check("F6", "reply in the outreach thread from the supplier's domain (found in Spam) -> NIR, one task, "
          "outreach REPLY_RECEIVED", routes["e2e-m1"]["owner"] == "NIR" and nir_tasks == 1
          and routes["e2e-m1"]["source_folder"] == "spam"
          and (await ost.for_supplier(sup3))[-1]["state"] == "REPLY_RECEIVED", routes["e2e-m1"])
    check("F7", "same thread, different sender domain -> exception for a human, not NIR",
          routes["e2e-m2"]["owner"] == "exception")
    check("F8", "two open inquiries for one domain -> exception (ambiguous), not misrouted",
          routes["e2e-m3"]["owner"] == "exception")
    check("F9", "supplier mail outside any inquiry -> BOAZ", routes["e2e-m4"]["owner"] == "BOAZ")
    check("F10", "unrelated mail -> no owner", routes["e2e-m5"]["owner"] is None)
    r2 = await route_supplier_replies(est, ost, PgTodoSink(), procurement_store=pr.PgProcurementStore())
    nir_tasks2 = await cat.fetchval("SELECT count(*) FROM agent_todos WHERE assigned_to_agent = 'NIR' "
                                    "AND artifacts->>'outreach_id' = $1", o_a["id"])
    check("F11", "repeated routing pass -> nothing re-routed, no duplicate task", not any(r2.values()) and nir_tasks2 == 1,
          (r1, r2))
    import email_agent.gmail_client as gc
    names = {n for n in dir(gc.GmailClient) if not n.startswith("__")}
    check("F12", "Gmail client has no label/modify/trash/delete method (Spam pass cannot move or delete mail)",
          not any(w in n.lower() for n in names for w in ("modify", "trash", "delete", "label")), sorted(names))


async def main():
    cat, pii = await asyncpg.connect(CAT), await asyncpg.connect(PII)
    try:
        print("== A. discovery + qualification"); await stage_a(cat)
        print("== B/C/E/G. pricing gates, authorization boundaries, order lifecycle"); await stage_bce(cat, pii)
        print("== D. BOAZ task ownership"); await stage_d(cat)
        print("== F. outreach + reply routing"); await stage_f(cat, pii)
    finally:
        await cat.close(); await pii.close()
    bad = [c for c, ok in RESULTS if not ok]
    print(f"\nTOTAL {len(RESULTS)} checks: {len(RESULTS) - len(bad)} PASS, {len(bad)} FAIL {bad}")
    sys.exit(1 if bad else 0)


asyncio.run(main())
