"""Supplier order connections (supplier_ordering.py) behind the procurement approvals - owner decision 2026-10-27.

Offline only: AliExpress is a fake HTTP function shaped like the OFFICIAL Open Platform reference
(aliexpress.ds.order.create / freight.query / afterpay / order.tracking.get, read 2026-10-27); Gmail is a fake.
No real order, payment or email. Every external boundary here is SIMULATED.
"""
import base64
import email as _email
import json
import uuid
from types import SimpleNamespace

import pytest

import procurement as pr
import supplier_authorization as au
import supplier_ordering as so
import supplier_qualification as q
from email_agent import supplier_routing as sr
from email_agent.gmail_client import (GmailClient, PurchaseOrderEmailAuthorization, SendProhibited,
                                      SupplierInquiryAuthorization, assert_allowed)
from email_agent.store import InMemoryStore

SHIP_TO = {"full_name": "Dana Levi", "street": "Herzl 1", "city": "Haifa", "postal_code": "3100000",
           "country": "IL", "phone": "054-1234567"}
SKU = {"sku_attr": "", "offer_sale_price": "14.24", "sku_id": "12000053138885208", "currency_code": "USD",
       "sku_price": "29.05", "sku_available_stock": 9}
FREIGHT = [{"code": "CAINIAO_FULFILLMENT_STD", "shipping_fee_currency": "USD", "free_shipping": "true",
            "tracking": "true", "shipping_fee_cent": "0", "max_delivery_days": "20"},
           {"code": "UNTRACKED_CHEAP", "shipping_fee_currency": "USD", "free_shipping": "true", "tracking": "false",
            "shipping_fee_cent": "0"}]


class FakeAE:
    """Answers like the official reference. Records every method called and its params."""

    def __init__(self, create=None, afterpay=True, skus=None, freight=None, status="WAIT_SELLER_SEND_GOODS"):
        self.calls = []
        self.create = create or {"is_success": "true", "order_list": [8100000001]}
        self.afterpay, self.skus, self.freight, self.status = afterpay, skus, freight, status

    async def post(self, data):
        m = data["method"]
        self.calls.append((m, data))
        if m == "aliexpress.ds.product.get":
            return 200, {"aliexpress_ds_product_get_response": {"result": {"ae_item_sku_info_dtos": {
                "ae_item_sku_info_d_t_o": self.skus if self.skus is not None else [SKU]}}}}
        if m == "aliexpress.ds.freight.query":
            return 200, {"aliexpress_ds_freight_query_response": {"result": {
                "success": "true", "delivery_options": self.freight if self.freight is not None else FREIGHT}}}
        if m == "aliexpress.ds.order.create":
            if isinstance(self.create, Exception):
                raise self.create
            return 200, {"aliexpress_ds_order_create_response": {"result": self.create}}
        if m == "aliexpress.ds.order.afterpay":
            return 200, {"aliexpress_ds_order_afterpay_response": {"result": "true" if self.afterpay else "false",
                                                                     "rsp_code": "200" if self.afterpay else "FAIL"}}
        if m == "aliexpress.trade.ds.order.get":
            return 200, {"aliexpress_trade_ds_order_get_response": {"result": {"order_status": self.status}}}
        if m == "aliexpress.ds.order.tracking.get":
            return 200, {"aliexpress_ds_order_tracking_get_response": {"result": {"ret": "true", "data": {
                "tracking_detail_line_list": [{"mail_no": "62727952231"}]}}}}
        raise AssertionError(f"unexpected method {m}")

    def methods(self):
        return [m for m, _ in self.calls]


class _Sup:
    async def _ensure_token(self):
        return True

    def _build_request(self, method, extra):
        return {"method": method, **extra}


def ae_connector(fake, price_usd=14.24):
    async def addr(_oid):
        return dict(SHIP_TO)

    async def offer(_sp):
        return {"supplier_url": "https://www.aliexpress.com/item/1005010669652047.html", "price_usd": price_usd,
                "supplier_sku": "ABC"}

    async def rate():
        return 3.7

    return so.AliExpressDsConnector(supplier=_Sup(), http_post=fake.post, address_loader=addr, offer_loader=offer,
                                    usd_rate=rate)


ITEMS = [{"supplier_part_id": "sp-1", "part_id": "p-1", "quantity": 1, "unit_cost": "52.69", "currency": "ILS"}]


async def _approved_row(store, auth, ship_cost=None, order="o1"):
    r = (await pr.prepare(store, order_id=order, order_number="AUTO-1", supplier_id="ae", items=ITEMS,
                          shipping={"cost": ship_cost, "currency": "ILS"}, checks={}))["procurement"]
    await pr.authorize_po(store, auth, r["id"], approver="owner", source="test")
    return r, pr.material_terms(order, "ae", ITEMS, {"cost": ship_cost, "currency": "ILS"})


# ═══ pure rules ════════════════════════════════════════════════════════════════════════════════════
def test_choose_sku_picks_exactly_one_or_refuses():
    assert so.choose_sku([SKU], 1, 14.24)["sku_id"] == SKU["sku_id"]
    assert not so.choose_sku([SKU], 10, 14.24)["ok"]                                 # stock 9 < 10
    assert "increased" in so.choose_sku([SKU], 1, 12.00)["reason"]
    assert not so.choose_sku([{**SKU, "currency_code": "EUR"}], 1, 14.24)["ok"]
    two = [SKU, {**SKU, "sku_id": "2", "sku_attr": "14:1", "offer_sale_price": "16.00"}]
    assert so.choose_sku(two, 1, 14.24)["sku_id"] == SKU["sku_id"]                   # exact price match
    assert not so.choose_sku(two, 1, 15.00)["ok"]                                     # no exact match -> refuse
    same = [SKU, {**SKU, "sku_id": "3", "sku_attr": "14:2"}]
    assert "cannot tell" in so.choose_sku(same, 1, 14.24)["reason"]


def test_choose_delivery_tracked_cheapest_within_approval():
    assert so.choose_delivery(FREIGHT, 0)["code"] == "CAINIAO_FULFILLMENT_STD"       # untracked never chosen
    paid = [{"code": "STD", "tracking": "true", "free_shipping": "false", "shipping_fee_cent": "4.50"}]
    assert not so.choose_delivery(paid, 0)["ok"] and so.choose_delivery(paid, 5)["fee_usd"] == 4.5
    assert not so.choose_delivery([FREIGHT[1]], 99)["ok"]


def test_address_mapping_and_product_id():
    a = so.ae_address(SHIP_TO)
    assert a["mobile_no"] == "541234567" and a["phone_country"] == "+972" and a["country"] == "IL"
    with pytest.raises(ValueError):
        so.ae_address({**SHIP_TO, "street": ""})
    assert so.ae_product_id("https://www.aliexpress.com/item/1005010669652047.html") == "1005010669652047"
    assert so.ae_product_id("https://example.com/x") is None


def test_connector_selection_and_master_switch(monkeypatch):
    monkeypatch.delenv("SUPPLIER_AUTO_ORDER_ENABLED", raising=False)
    assert isinstance(so.connector_for({"name": "AliExpress", "is_active": True}), so.ManualConnector)               # off by default
    pend = {"name": "ASAP Network", "credentials": {"status": "pending_credentials", "needs": "ASAP token"}}
    assert isinstance(so.connector_for(pend), so.AwaitingCredentialsConnector)
    monkeypatch.setenv("SUPPLIER_AUTO_ORDER_ENABLED", "1")
    assert isinstance(so.connector_for({"name": "AliExpress", "is_active": True}), so.AliExpressDsConnector)
    assert isinstance(so.connector_for({"name": "RockAuto", "is_active": True}), so.ManualConnector)
    qual = q.new_record("s")
    qual["requirements"]["contacts"]["details"] = {"contacts": [
        {"address": "orders@shop.example", "kind": "orders", "status": q.VERIFIED, "source": "https://shop.example/c"}]}
    email_sup = {"name": "Shop", "is_active": True, "credentials": {"ordering": "email", "qualification": qual}}
    # not DROPSHIPPING-READY -> no emailed purchase order (it would ship straight to our customer)
    assert "DROPSHIPPING-READY" in so.connector_for(email_sup).reason
    for k in q.DROPSHIP_MANDATORY + q.DROPSHIP_CORE:
        q.set_requirement(qual, k, q.VERIFIED, evidence_kind="official_policy_page", source=f"https://shop.example/{k}",
                          note="documented", details={"data_timestamp": __import__("datetime").datetime.now(
                              __import__("datetime").timezone.utc).isoformat()} if k == "availability" else {})
    qual["requirements"]["contacts"]["details"] = {"contacts": [
        {"address": "orders@shop.example", "kind": "orders", "status": q.VERIFIED, "source": "https://shop.example/c"}]}
    c = so.connector_for(email_sup)
    assert isinstance(c, so.EmailPurchaseOrderConnector) and c.recipient == "orders@shop.example"
    qual["requirements"]["contacts"]["details"]["contacts"][0]["status"] = q.UNVERIFIED
    assert isinstance(so.connector_for(email_sup), so.ManualConnector)


# ═══ AliExpress through procurement ════════════════════════════════════════════════════════════════
async def test_aliexpress_order_is_created_unpaid_with_our_idempotency_key():
    store, auth, fake = pr.InMemoryProcurementStore(), au.InMemoryAuthStore(), FakeAE()
    row, terms = await _approved_row(store, auth)
    res = await pr.submit_po(store, auth, row["id"], ae_connector(fake), terms)
    assert res["submitted"] and res["supplier_reference"] == "8100000001"
    assert fake.methods() == ["aliexpress.ds.product.get", "aliexpress.ds.freight.query", "aliexpress.ds.order.create"]
    sent = fake.calls[-1][1]
    dto, ext = json.loads(sent["param_place_order_request4_open_api_d_t_o"]), json.loads(sent["ds_extend_request"])
    assert dto["out_order_id"] == row["id"] and ext["payment"]["try_to_pay"] == "false"         # never pays here
    assert dto["product_items"][0]["logistics_service_name"] == "CAINIAO_FULFILLMENT_STD"
    assert dto["logistics_address"]["country"] == "IL"
    assert store.rows[row["id"]]["state"] == "SUBMITTED"
    assert "aliexpress.ds.order.afterpay" not in fake.methods()


async def test_aliexpress_refusals_never_reach_order_create():
    for fake, why in ((FakeAE(skus=[{**SKU, "offer_sale_price": "20.00"}]), "increased"),
                      (FakeAE(freight=[{"code": "X", "tracking": "true", "free_shipping": "false",
                                        "shipping_fee_cent": "6"}]), "exceeds")):
        store, auth = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()
        row, terms = await _approved_row(store, auth)
        res = await pr.submit_po(store, auth, row["id"], ae_connector(fake), terms)
        assert not res["submitted"] and why in res["reason"]
        assert "aliexpress.ds.order.create" not in fake.methods()
        assert store.rows[row["id"]]["state"] == "SUBMISSION_FAILED"


async def test_approved_shipping_raises_the_cap():
    store, auth = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()
    fake = FakeAE(freight=[{"code": "STD", "tracking": "true", "free_shipping": "false", "shipping_fee_cent": "6"}])
    row, terms = await _approved_row(store, auth, ship_cost="37.00")                 # 37 ILS / 3.7 = $10 approved
    assert (await pr.submit_po(store, auth, row["id"], ae_connector(fake), terms))["submitted"]


async def test_duplicate_and_ambiguous_results_are_never_resubmitted():
    store, auth = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()
    fake = FakeAE(create={"is_success": "false", "error_code": "REPEATED_ORDER_ERROR"})
    row, terms = await _approved_row(store, auth)
    conn = ae_connector(fake)
    await pr.submit_po(store, auth, row["id"], conn, terms)
    assert store.rows[row["id"]]["state"] == "EXCEPTION"
    again = await pr.submit_po(store, auth, row["id"], conn, terms)
    assert not again["submitted"] and fake.methods().count("aliexpress.ds.order.create") == 1
    # a reply with no result block = unknown
    store2, auth2, fake2 = pr.InMemoryProcurementStore(), au.InMemoryAuthStore(), FakeAE(create={})
    fake2.create = None
    row2, t2 = await _approved_row(store2, auth2)

    async def no_result(data):
        if data["method"] == "aliexpress.ds.order.create":
            return 200, {"aliexpress_ds_order_create_response": {}}
        return await FakeAE().post(data)

    c2 = ae_connector(fake2)
    c2._post = no_result
    await pr.submit_po(store2, auth2, row2["id"], c2, t2)
    assert store2.rows[row2["id"]]["state"] == "EXCEPTION"


async def test_definite_refusal_is_submission_failed():
    store, auth = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()
    fake = FakeAE(create={"is_success": "false", "error_code": "B_DROPSHIPPER_DELIVERY_ADDRESS_VALIDATE_FAIL"})
    row, terms = await _approved_row(store, auth)
    res = await pr.submit_po(store, auth, row["id"], ae_connector(fake), terms)
    assert not res["submitted"] and "ADDRESS_VALIDATE_FAIL" in res["reason"]
    assert store.rows[row["id"]]["state"] == "SUBMISSION_FAILED"


async def test_payment_is_separate_one_shot_and_blocks_the_second_payment_path(monkeypatch):
    store, auth, fake = pr.InMemoryProcurementStore(), au.InMemoryAuthStore(), FakeAE()
    row, terms = await _approved_row(store, auth)
    conn = ae_connector(fake)
    await pr.submit_po(store, auth, row["id"], conn, terms)
    refused = await pr.pay_via_connector(store, auth, row["id"], conn, terms)            # no payment approval
    assert not refused["paid"] and "aliexpress.ds.order.afterpay" not in fake.methods()
    await pr.authorize_payment(store, auth, row["id"], approver="owner", source="test")
    assert (await pr.pay_via_connector(store, auth, row["id"], conn, terms))["paid"]
    assert store.rows[row["id"]]["state"] == "SUPPLIER_PAID"
    assert not (await pr.pay_via_connector(store, auth, row["id"], conn, terms))["paid"]  # never twice
    assert fake.methods().count("aliexpress.ds.order.afterpay") == 1
    # the fulfillment spend path refuses an order paid through the connection

    async def terms_for(order, items):
        return {"ae": {"items": ITEMS, "shipping": {"cost": None}, "checks": {}, "terms": terms}}

    monkeypatch.setattr(pr, "current_terms_for_order", terms_for)
    why = await pr.gate_paid_order(SimpleNamespace(id="o1", order_number="AUTO-1"), [], store=store, auth_store=auth)
    assert why and "no second payment" in why


async def test_failed_afterpay_keeps_the_approval_for_a_retry():
    store, auth = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()
    fake = FakeAE(afterpay=False)
    row, terms = await _approved_row(store, auth)
    conn = ae_connector(fake)
    await pr.submit_po(store, auth, row["id"], conn, terms)
    await pr.authorize_payment(store, auth, row["id"], approver="owner", source="test")
    assert not (await pr.pay_via_connector(store, auth, row["id"], conn, terms))["paid"]
    fake.afterpay = True
    assert (await pr.pay_via_connector(store, auth, row["id"], conn, terms))["paid"]


async def test_reconcile_confirms_and_records_tracking():
    store, auth, fake = pr.InMemoryProcurementStore(), au.InMemoryAuthStore(), FakeAE()
    row, terms = await _approved_row(store, auth)
    conn = ae_connector(fake)
    await pr.submit_po(store, auth, row["id"], conn, terms)
    r = await pr.reconcile(store, row["id"], conn)
    assert r["changed"] and store.rows[row["id"]]["state"] == "SUPPLIER_CONFIRMED"
    assert store.rows[row["id"]]["tracking_number"] == "62727952231"


async def test_transport_errors_before_and_after_sending(monkeypatch):
    import httpx

    real = httpx.AsyncClient                          # captured once: each fake wraps the REAL client

    def factory(exc):

        def make(*a, **kw):
            def handler(request):
                raise exc
            kw["transport"] = httpx.MockTransport(handler)
            return real(*a, **kw)
        return make

    conn = so.AliExpressDsConnector(supplier=_Sup())
    monkeypatch.setattr(httpx, "AsyncClient", factory(httpx.ConnectError("refused")))
    assert "nothing sent" in (await conn._call("aliexpress.ds.order.create", {}))["_error"]
    monkeypatch.setattr(httpx, "AsyncClient", factory(httpx.ReadTimeout("slow")))
    with pytest.raises(so.AmbiguousSubmission):
        await conn._call("aliexpress.ds.order.create", {})


# ═══ purchase order by email ═══════════════════════════════════════════════════════════════════════
class FakeGmail:
    def __init__(self, tamper=False):
        self.drafts, self.sent, self.tamper = {}, [], tamper

    def create_new_draft(self, raw, thread_id=None):
        did = f"d{len(self.drafts) + 1}"
        self.drafts[did] = raw
        return {"id": did}

    def get_draft(self, did):
        msg = _email.message_from_bytes(base64.urlsafe_b64decode(self.drafts[did]))
        body = msg.get_payload(decode=True).decode()
        if self.tamper:
            body += "\nAlso add 10 more units."
        return {"id": did, "message": {"id": "m", "threadId": "t", "labelIds": ["DRAFT"], "payload": {
            "mimeType": "text/plain", "headers": [{"name": "To", "value": msg["To"]}, {"name": "From", "value": msg["From"]}],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}}}

    def send_purchase_order_email(self, auth):
        assert isinstance(auth, PurchaseOrderEmailAuthorization) and auth.authorization_id
        self.sent.append(auth.draft_id)
        return {"id": "sent-1", "threadId": "po-thread-1", "labelIds": ["SENT"]}


def email_connector(gm):
    async def addr(_o):
        return dict(SHIP_TO)

    async def offer(_s):
        return {"supplier_sku": "BRK-123"}

    return so.EmailPurchaseOrderConnector("orders@shop.example", client=gm, sender="biz@example.com",
                                          address_loader=addr, offer_loader=offer)


async def test_email_purchase_order_is_fixed_text_sent_once_and_replies_go_to_boaz():
    store, auth, gm = pr.InMemoryProcurementStore(), au.InMemoryAuthStore(), FakeGmail()
    row, terms = await _approved_row(store, auth)
    res = await pr.submit_po(store, auth, row["id"], email_connector(gm), terms)
    assert res["submitted"] and gm.sent == ["d1"]
    body = base64.urlsafe_b64decode(gm.drafts["d1"]).decode()
    assert "BRK-123 x 1 at 52.69 ILS" in body and "covers only the items" in body
    ev = store.rows[row["id"]]["submission_evidence"]
    assert ev["gmail_thread_id"] == "po-thread-1" and ev["awaiting_supplier_confirmation"]
    # the supplier's reply in that thread -> a BOAZ task; another sender domain -> exception
    es, todos = InMemoryStore(), _Todos()
    ok = _row(es, thread="po-thread-1", sender="sales@shop.example")
    bad = _row(es, thread="po-thread-1", sender="x@phish.example")
    out = await sr.route_supplier_replies(es, _NoOutreach(), todos, procurement_store=store)
    assert out["to_boaz"] == 1 and out["exceptions"] == 1
    assert todos.todos[0]["agent"] == "BOAZ" and todos.todos[0]["artifacts"]["kind"] == "procurement_reply"
    assert ok["supplier_route"]["procurement_id"] == row["id"] and bad["requires_human"]


async def test_tampered_purchase_order_draft_is_not_sent():
    store, auth, gm = pr.InMemoryProcurementStore(), au.InMemoryAuthStore(), FakeGmail(tamper=True)
    row, terms = await _approved_row(store, auth)
    res = await pr.submit_po(store, auth, row["id"], email_connector(gm), terms)
    assert not res["submitted"] and gm.sent == [] and store.rows[row["id"]]["state"] == "SUBMISSION_FAILED"


def test_gmail_purchase_order_path_is_separate():
    assert_allowed("POST", "/drafts/send", purchase_order=True)
    for path in ("/messages/send", "/drafts", "/messages/x/modify"):
        with pytest.raises(SendProhibited):
            assert_allowed("POST", path, purchase_order=True)
    with pytest.raises(SendProhibited):
        assert_allowed("POST", "/drafts/send", purchase_order=True, supplier_inquiry=True)
    client = GmailClient.__new__(GmailClient)
    with pytest.raises(SendProhibited):        # an inquiry authorization is not a purchase-order authorization
        client.send_purchase_order_email(SupplierInquiryAuthorization("o", "d", "a", "x@y.z", "h"))
    with pytest.raises(SendProhibited):
        client.send_purchase_order_email(PurchaseOrderEmailAuthorization("p", "d", "", "x@y.z", "h"))


# ═══ console ═══════════════════════════════════════════════════════════════════════════════════════
async def test_console_approve_submits_through_the_connection_then_pays_only_on_its_own_approval():
    from agents import supplier_console as sc
    store, auth, fake = pr.InMemoryProcurementStore(), au.InMemoryAuthStore(), FakeAE()
    row = (await pr.prepare(store, order_id="o1", order_number="AUTO-1", supplier_id="ae", items=ITEMS,
                            shipping={"cost": None, "currency": "ILS"}, checks={}))["procurement"]
    terms = pr.material_terms("o1", "ae", ITEMS, {"cost": None, "currency": "ILS"})

    async def conn(_s):
        return ae_connector(fake)

    async def tf(_r):
        return terms

    kw = {"procurement_store": store, "auth_store": auth, "connector_factory": conn, "terms_fn": tf}
    out = await sc.handle(f"אשררכש {row['id'][:8]}", **kw)
    assert "8100000001" in out and "עדיין לא שולם" in out
    assert "aliexpress.ds.order.afterpay" not in fake.methods()
    out = await sc.handle(f"אשרתשלוםספק {row['id'][:8]}", **kw)
    assert "שולם אצל הספק" in out and fake.methods().count("aliexpress.ds.order.afterpay") == 1
    out = await sc.handle(f"בדוקרכש {row['id'][:8]}", **kw)
    assert "62727952231" in out


# ── helpers shared with routing tests ──────────────────────────────────────────────────────────────
class _Todos:
    def __init__(self):
        self.todos = []

    async def create(self, agent, title, description, priority, artifacts):
        self.todos.append({"agent": agent, "artifacts": artifacts})
        return f"task-{len(self.todos)}"


class _NoOutreach:
    async def by_thread(self, _t):
        return []

    async def awaiting_by_recipient_domain(self, _d):
        return []


def _row(store, *, thread, sender):
    rid = str(uuid.uuid4())
    mid = "m" + rid[:6]
    store.rows[mid] = {"id": rid, "gmail_message_id": mid, "gmail_thread_id": thread, "sender_email": sender,
                       "status": "processed", "classification": "unknown", "created_at": __import__("datetime").datetime(2026, 10, 27), "task_ids": [],
                       "source_folder": "inbox", "supplier_routed_at": None, "supplier_route": None,
                       "requires_human": False}
    return store.rows[mid]
