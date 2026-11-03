"""Pre-deployment safety gates for the supplier-lifecycle candidate (owner task 2026-10-27, "MASTER EXECUTION TASK").

Offline only. Every supplier, Gmail and payment interaction is a fake: SIMULATED. Nothing real is sent, ordered or paid.
"""
import ast
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import procurement as pr
import supplier_authorization as au
import supplier_flags as sf
import supplier_ordering as so
import supplier_outreach as out
import supplier_qualification as q
from email_agent.store import InMemoryStore
from tests.test_supplier_ordering import FakeAE, ITEMS, ae_connector

BACKEND = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


# ═══ TODO 1: nothing activates by itself ═══════════════════════════════════════════════════════════
@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", "on", " 1", "1 ", "01", "TRUE"])
def test_every_flag_is_off_unless_exactly_1(value):
    env = {} if value is None else {n: value for n in sf.FLAGS}
    assert not any(sf.snapshot(env).values())


def test_flag_on_only_with_exact_value_and_unknown_flag_is_an_error():
    assert sf.enabled("BOAZ_TASKS_ENABLED", {"BOAZ_TASKS_ENABLED": "1"})
    with pytest.raises(KeyError):
        sf.enabled("SOMETHING_ELSE", {})


def test_spam_scan_is_off_by_default():
    from email_agent.config import load_config
    base = {"EMAIL_AGENT_ENABLED": "1", "GMAIL_OAUTH_CLIENT_ID": "x", "GMAIL_OAUTH_CLIENT_SECRET": "y",
            "GMAIL_OAUTH_REFRESH_TOKEN": "z"}
    assert load_config(base).scan_spam is False
    assert load_config({**base, "EMAIL_AGENT_SCAN_SPAM": "true"}).scan_spam is False
    assert load_config({**base, "EMAIL_AGENT_SCAN_SPAM": "1"}).scan_spam is True


def test_every_new_capability_reads_the_single_flag_module():
    src = {p: (BACKEND / p).read_text() for p in ("BACKEND_API_ROUTES.py", "email_agent/loop.py",
                                                    "email_agent/config.py", "supplier_outreach.py",
                                                    "supplier_ordering.py")}
    assert '_sf.enabled("BOAZ_TASKS_ENABLED")' in src["BACKEND_API_ROUTES.py"]
    assert 'os.getenv("BOAZ_TASKS_ENABLED"' not in src["BACKEND_API_ROUTES.py"]
    assert '_flags.enabled("SUPPLIER_ROUTING_ENABLED")' in src["email_agent/loop.py"]
    assert '_flags.enabled("EMAIL_AGENT_SCAN_SPAM", env)' in src["email_agent/config.py"]
    assert 'supplier_flags.enabled("NIR_OUTREACH_ENABLED")' in src["supplier_outreach.py"]
    assert 'supplier_flags.enabled("SUPPLIER_AUTO_ORDER_ENABLED")' in src["supplier_ordering.py"]
    for p, s in src.items():
        for n in sf.FLAGS:
            assert f'os.getenv("{n}"' not in s and f"os.environ.get(\"{n}\"" not in s, (p, n)


def test_boaz_loop_registered_once_and_idles_with_flag_unset():
    src = (BACKEND / "BACKEND_API_ROUTES.py").read_text()
    assert src.count('_supervised_task("boaz_task_loop"') == 1
    body = src[src.index("async def _boaz_task_loop"):src.index("async def _supplier_sourcing_loop")]
    assert body.index('_sf.enabled("BOAZ_TASKS_ENABLED")') < body.index("_bt.run_once(")


async def test_email_loop_skips_supplier_features_on_old_schema_and_when_off(monkeypatch, tmp_path):
    from email_agent import loop as loop_mod
    from email_agent import control as control_mod
    monkeypatch.setenv("EMAIL_AGENT_CONTROL_FILE", str(tmp_path / "ctl.json"))
    control_mod.set_mode(control_mod.RUN, by="test")
    called = []
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 2:
            raise KeyboardInterrupt

    async def fake_cycle(cfg, client, store, source, **kw):
        return {"state": "configured", "connection": {"ok": True}, "errors": []}

    class Store:
        def __init__(self, ready):
            self.ready = ready

        async def supplier_schema_ready(self):
            return self.ready

    async def routing(*a, **k):
        called.append("routing")
        return {}

    async def outreach_cycle(*a, **k):
        called.append("outreach")
        return {}

    async def hb(_s):
        return None

    monkeypatch.setenv("EMAIL_AGENT_ENABLED", "1")
    for k, v in (("GMAIL_OAUTH_CLIENT_ID", "c"), ("GMAIL_OAUTH_CLIENT_SECRET", "s"), ("GMAIL_OAUTH_REFRESH_TOKEN", "r")):
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(loop_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(loop_mod, "process_once", fake_cycle)
    monkeypatch.setattr(loop_mod, "_heartbeat", hb)
    monkeypatch.setattr(loop_mod, "GmailClient", lambda *a, **k: object())
    monkeypatch.setattr(loop_mod, "DbContextSource", lambda *a, **k: object())
    monkeypatch.setattr(loop_mod, "PgOutreachStore", lambda *a, **k: object())
    monkeypatch.setattr(loop_mod, "route_supplier_replies", routing)
    monkeypatch.setattr(loop_mod._outreach, "run_outreach_cycle", outreach_cycle)
    for ready, flags in ((True, {}), (False, {"SUPPLIER_ROUTING_ENABLED": "1", "NIR_OUTREACH_ENABLED": "1"})):
        for n in sf.FLAGS:
            monkeypatch.delenv(n, raising=False)
        for k, v in flags.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setattr(loop_mod, "PgStore", lambda *a, _r=ready, **k: Store(_r))
        sleeps.clear()
        with pytest.raises(KeyboardInterrupt):
            await loop_mod.email_agent_loop()
    assert called == []                 # flags off -> nothing; flags on but schema not migrated -> nothing


async def test_store_refuses_supplier_columns_on_an_old_schema():
    from email_agent.store import PgStore, SupplierSchemaNotReady
    st = PgStore()

    async def not_ready():
        return False

    st.supplier_schema_ready = not_ready
    for call in (st.supplier_route_pending(5), st.set_source_folder("m", "spam"), st.set_supplier_route("i", {})):
        with pytest.raises(SupplierSchemaNotReady):
            await call


# ═══ TODO 3: Gmail - no message is lost behind already-processed ones ═══════════════════════════════
class PagingClient:
    def __init__(self, ids, page=25):
        self.ids, self.page, self.pages = ids, page, 0

    def list_message_page(self, query, n, token=None, include_spam=False):
        self.pages += 1
        start = int(token or 0)
        chunk = self.ids[start:start + self.page]
        nxt = start + self.page
        return [{"id": i, "threadId": "t" + i} for i in chunk], (str(nxt) if nxt < len(self.ids) else None)


async def test_unprocessed_mail_behind_a_full_page_is_found():
    from email_agent.agent import unprocessed_refs
    store = InMemoryStore()
    ids = [f"m{i:03d}" for i in range(60)]
    for i in ids[:40]:                                       # newest 40 already processed
        store.rows[i] = {"id": str(uuid.uuid4()), "status": "processed"}
    store.rows["m005"]["status"] = "retry"                   # a retry is still picked up
    client = PagingClient(ids)
    r = await unprocessed_refs(client, store, "in:inbox", 25)
    got = [x["id"] for x in r["refs"]]
    assert got[0] == "m005" and got[1:] == ids[40:60] and r["pages"] == 3 and r["exhausted"]


async def test_scan_is_bounded_and_reports_backlog():
    from email_agent.agent import unprocessed_refs
    store = InMemoryStore()
    ids = [f"m{i:04d}" for i in range(500)]
    for i in ids[:400]:
        store.rows[i] = {"id": i, "status": "processed"}
    r = await unprocessed_refs(PagingClient(ids), store, "q", 25, max_pages=5)
    assert r["pages"] == 5 and r["refs"] == [] and not r["exhausted"]      # bounded; continues next cycle


async def test_ignored_spam_does_not_hide_relevant_older_spam():
    from email_agent.agent import unprocessed_refs
    store = InMemoryStore()
    ids = [f"s{i:03d}" for i in range(40)]
    ignored = {i: True for i in ids[:30]}                     # newest 30 already judged irrelevant
    r = await unprocessed_refs(PagingClient(ids), store, "in:spam", 25, include_spam=True, skip_ids=ignored)
    assert [x["id"] for x in r["refs"]] == ids[30:]


# ═══ TODO 5: dropshipping qualification ═════════════════════════════════════════════════════════════
def _all_verified(rec, keys, now=NOW):
    for k in keys:
        q.set_requirement(rec, k, q.VERIFIED, evidence_kind="official_policy_page", source=f"https://s.example/{k}",
                          note=f"{k} documented", now=now,
                          details={"data_timestamp": now.isoformat()} if k == "availability" else {})


def test_dropshipping_ready_needs_direct_shipping_and_israel_delivery_verified():
    rec = q.new_record("s1", NOW)
    _all_verified(rec, q.DROPSHIP_MANDATORY + q.DROPSHIP_CORE)
    c = q.classify(rec, NOW)
    assert c["dropshipping"] == q.DROPSHIP_READY
    assert c["classification"] == q.NEEDS_INVESTIGATION and not c["integration_ready"]   # API is separate


def test_dropship_verified_but_israel_delivery_unverified_is_candidate():
    rec = q.new_record("s1", NOW)
    _all_verified(rec, [k for k in q.DROPSHIP_MANDATORY + q.DROPSHIP_CORE if k != "dropship_israel_delivery"])
    c = q.classify(rec, NOW)
    assert c["dropshipping"] == q.DROPSHIP_CANDIDATE and "dropship_israel_delivery" in c["dropship_unverified"]


def test_israel_shipping_verified_but_dropship_failed():
    rec = q.new_record("s1", NOW)
    _all_verified(rec, q.DROPSHIP_CORE)
    q.set_requirement(rec, "dropship_direct_to_customer", q.FAILED, evidence_kind="official_policy_page",
                      source="https://s.example/b2b", note="ships only to the buyer's own address", now=NOW)
    c = q.classify(rec, NOW)
    assert c["dropshipping"] == q.DROPSHIP_FAILED and c["dropship_failed"] == ["dropship_direct_to_customer"]


def test_supplier_claims_never_make_dropshipping_ready_and_contradictions_block():
    rec = q.new_record("s1", NOW)
    _all_verified(rec, q.DROPSHIP_CORE)
    for k in q.DROPSHIP_MANDATORY:
        q.record_supplier_claim(rec, k, "yes we dropship to Israel", value=True, source_ref="email:abcd")
    assert q.classify(rec, NOW)["dropshipping"] == q.DROPSHIP_CANDIDATE
    _all_verified(rec, q.DROPSHIP_MANDATORY)
    q.record_supplier_claim(rec, "dropship_israel_delivery", "we no longer ship to Israel", value=False,
                            source_ref="email:efgh")
    assert q.classify(rec, NOW)["dropshipping"] == q.DROPSHIP_CANDIDATE                  # contradiction surfaced


def test_non_api_supplier_can_be_dropshipping_ready_but_never_integration_ready():
    rec = q.new_record("s1", NOW)
    _all_verified(rec, q.DROPSHIP_MANDATORY + q.DROPSHIP_CORE)
    q.set_requirement(rec, "api", q.FAILED, evidence_kind="official_documentation", source="https://s.example/faq",
                      note="no API", now=NOW)
    c = q.classify(rec, NOW)
    assert c["dropshipping"] == q.DROPSHIP_READY and c["classification"] == q.NO_API and not c["integration_ready"]


def test_stale_stock_evidence_blocks_dropshipping_ready():
    rec = q.new_record("s1", NOW)
    _all_verified(rec, q.DROPSHIP_MANDATORY + q.DROPSHIP_CORE, now=NOW - timedelta(days=60))
    assert q.classify(rec, NOW)["dropshipping"] == q.DROPSHIP_CANDIDATE


def test_v1_records_get_dropshipping_unverified_never_passed():
    v1 = q.new_record("s1", NOW)
    for k in q.DROPSHIP_REQUIREMENTS:
        v1["requirements"].pop(k)
    v1["schema"] = 1
    c = q.classify(v1, NOW)
    assert c["dropshipping"] == q.DROPSHIP_CANDIDATE
    assert all(v1["requirements"][k]["status"] == q.UNVERIFIED for k in q.DROPSHIP_REQUIREMENTS)
    assert v1["schema"] == q.SCHEMA_VERSION


def test_outreach_asks_dropshipping_questions_within_scope():
    rec = q.new_record("s1", NOW)
    purposes = out.purposes_for_gaps(rec)
    assert "dropshipping_terms" in purposes
    r = out.render_inquiry(purposes)
    out.check_scope(r["purposes"], r["body"])                                           # still non-binding


def test_email_ordering_requires_dropshipping_ready(monkeypatch):
    monkeypatch.setenv("SUPPLIER_AUTO_ORDER_ENABLED", "1")
    rec = q.new_record("s1", NOW)
    _all_verified(rec, q.DROPSHIP_CORE)
    rec["requirements"]["contacts"]["details"] = {"contacts": [
        {"address": "o@s.example", "kind": "orders", "status": q.VERIFIED, "source": "https://s.example/c"}]}
    sup = {"name": "S", "is_active": True, "credentials": {"ordering": "email", "qualification": rec}}
    assert isinstance(so.connector_for(sup), so.ManualConnector)                         # dropship unverified
    _all_verified(rec, q.DROPSHIP_MANDATORY, now=datetime.now(timezone.utc))
    _all_verified(rec, ["availability"], now=datetime.now(timezone.utc))
    assert isinstance(so.connector_for(sup), so.EmailPurchaseOrderConnector)


async def test_boaz_handoff_reports_dropshipping_status_and_never_activates():
    import boaz_tasks as bt
    recorded = {}

    async def status(sid):
        return {"exists": True, "is_active": True, "priced_offers": 3, "qualification_unverified": [],
                "dropshipping": q.DROPSHIP_CANDIDATE}

    async def rec_(sid, entry):
        recorded[sid] = entry

    task = {"id": str(uuid.uuid4()), "assigned_to_agent": "BOAZ", "status": "not_started", "title": "t",
            "category": "supplier", "artifacts": {"kind": "supplier_handoff", "supplier_id": "s1"}, "metrics": {}}
    out_ = await bt.run_once(bt.InMemoryTaskRepo([task]), {"supplier_status": status, "record_onboarding": rec_})
    assert out_["escalated"] == 1 and any("dropshipping" in m for m in recorded["s1"]["missing"])


# ═══ TODO 6: AliExpress last-moment checks, duplicates, concurrency ═════════════════════════════════
def test_missing_shipping_fee_is_not_free():
    opts = [{"code": "A", "tracking": "true", "free_shipping": "false"},                  # no fee field
            {"code": "B", "tracking": "true", "free_shipping": "false", "shipping_fee_cent": "null"}]
    assert not so.choose_delivery(opts, 0)["ok"]
    assert not so.choose_delivery(opts, 99)["ok"]                                       # never a guessed fee
    assert so.choose_delivery([{"code": "C", "tracking": "true", "free_shipping": "true"}], 0)["ok"]


async def _ae_row(store, auth, order="o1"):
    r = (await pr.prepare(store, order_id=order, order_number="A-1", supplier_id="ae", items=ITEMS,
                          shipping={"cost": None, "currency": "ILS"}, checks={}))["procurement"]
    await pr.authorize_po(store, auth, r["id"], approver="owner", source="test")
    return r, pr.material_terms(order, "ae", ITEMS, {"cost": None, "currency": "ILS"})


async def test_concurrent_submissions_create_one_order():
    store, auth, fake = pr.InMemoryProcurementStore(), au.InMemoryAuthStore(), FakeAE()
    row, terms = await _ae_row(store, auth)
    slow_conn = ae_connector(fake)
    orig = fake.post

    async def slow(data):
        await asyncio.sleep(0.01)
        return await orig(data)

    slow_conn._post = slow
    results = await asyncio.gather(*[pr.submit_po(store, auth, row["id"], slow_conn, terms) for _ in range(5)])
    assert sum(1 for r in results if r["submitted"]) == 1
    assert fake.methods().count("aliexpress.ds.order.create") == 1


async def test_concurrent_payments_pay_once():
    store, auth, fake = pr.InMemoryProcurementStore(), au.InMemoryAuthStore(), FakeAE()
    row, terms = await _ae_row(store, auth)
    conn = ae_connector(fake)
    await pr.submit_po(store, auth, row["id"], conn, terms)
    await pr.authorize_payment(store, auth, row["id"], approver="owner", source="test")
    orig = fake.post

    async def slow(data):
        await asyncio.sleep(0.01)
        return await orig(data)

    conn._post = slow
    res = await asyncio.gather(*[pr.pay_via_connector(store, auth, row["id"], conn, terms) for _ in range(5)])
    assert sum(1 for r in res if r["paid"]) == 1 and fake.methods().count("aliexpress.ds.order.afterpay") == 1


async def test_crash_mid_submission_needs_reconciliation_then_manual_record():
    store, auth = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()
    row, terms = await _ae_row(store, auth)
    assert await store.claim(row["id"], "PO_AUTHORIZED", "in_flight", {})               # process died here
    fake = FakeAE()
    r = await pr.submit_po(store, auth, row["id"], ae_connector(fake), terms)
    assert not r["submitted"] and "reconcile" in r["reason"] and fake.calls == []
    await pr.record_manual_submission(store, auth, row["id"], "8100000009", approver="owner")
    assert store.rows[row["id"]]["state"] == "SUBMITTED" and store.rows[row["id"]]["supplier_reference"] == "8100000009"


async def test_status_check_is_read_only():
    store, auth, fake = pr.InMemoryProcurementStore(), au.InMemoryAuthStore(), FakeAE()
    row, terms = await _ae_row(store, auth)
    conn = ae_connector(fake)
    await pr.submit_po(store, auth, row["id"], conn, terms)
    before = list(fake.methods())
    for _ in range(3):
        await pr.reconcile(store, row["id"], conn)
    new_calls = fake.methods()[len(before):]
    assert set(new_calls) == {"aliexpress.trade.ds.order.get", "aliexpress.ds.order.tracking.get"}
    assert "aliexpress.ds.order.create" not in new_calls and "aliexpress.ds.order.afterpay" not in new_calls


async def test_replayed_wrong_entity_and_changed_terms_approvals_are_refused():
    store, auth, fake = pr.InMemoryProcurementStore(), au.InMemoryAuthStore(), FakeAE()
    a, ta = await _ae_row(store, auth, order="oA")
    b = (await pr.prepare(store, order_id="oB", order_number="B", supplier_id="ae", items=ITEMS,
                          shipping={"cost": None}, checks={}))["procurement"]
    tb = pr.material_terms("oB", "ae", ITEMS, {"cost": None})
    conn = ae_connector(fake)
    assert not (await pr.submit_po(store, auth, b["id"], conn, tb))["submitted"]           # A's approval != B
    assert (await pr.submit_po(store, auth, a["id"], conn, ta))["submitted"]
    assert not (await pr.submit_po(store, auth, a["id"], conn, ta))["submitted"]           # replay
    await pr.authorize_payment(store, auth, a["id"], approver="owner", source="test")
    changed = pr.material_terms("oA", "ae", [{**ITEMS[0], "unit_cost": "99.00"}], {"cost": None})
    assert not (await pr.pay_via_connector(store, auth, a["id"], conn, changed))["paid"]   # changed terms
    assert fake.methods().count("aliexpress.ds.order.create") == 1
    assert "aliexpress.ds.order.afterpay" not in fake.methods()


async def test_customer_payment_alone_never_orders_or_pays(monkeypatch):
    store, auth = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()

    async def terms_for(order, items):
        return {"ae": {"items": ITEMS, "shipping": {"cost": None}, "checks": {},
                       "terms": pr.material_terms("o1", "ae", ITEMS, {"cost": None})}}

    monkeypatch.setattr(pr, "current_terms_for_order", terms_for)
    monkeypatch.delenv("PROCUREMENT_GATE_SANDBOX_BYPASS", raising=False)
    for _ in range(4):                                                                    # duplicate webhooks
        assert await pr.gate_paid_order(SimpleNamespace(id="o1", order_number="A"), [], store=store, auth_store=auth)
    row = next(iter(store.rows.values()))
    assert row["state"] == "AWAITING_PO_AUTHORIZATION" and auth.rows == []


def test_every_supplier_spend_call_sits_behind_the_gate():
    src = (BACKEND / "routes" / "utils.py").read_text()
    tree = ast.parse(src)
    fns = [(n.name, n.lineno, n.end_lineno) for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    lines = src.splitlines()
    gate = next(i for i, l in enumerate(lines, 1) if "_proc.gate_paid_order(order_db" in l)
    tsf = next(f for f in fns if f[0] == "trigger_supplier_fulfillment")
    spend = [i for i, l in enumerate(lines, 1) if "PaymentIntent.create(" in l or
             ("_create_test_issuing_authorization_https(" in l and "def " not in l)]
    assert spend and all(tsf[1] <= i <= tsf[2] and i > gate for i in spend)
    # the Issuing service module has no caller anywhere in the app
    hits = [p for p in BACKEND.rglob("*.py") if "tests" not in p.parts and "stripe_issuing_service" in p.read_text(errors="ignore")
            and p.name != "stripe_issuing_service.py"]
    assert hits == []


# ═══ TODO 7: email purchase order text ══════════════════════════════════════════════════════════════
def test_purchase_order_text_carries_shipping_terms_and_approval_reference():
    row = {"id": "abcdef12-x", "terms_hash": "0123456789abcdef", "terms": {"shipping": {"cost": None}},
           "items": [{"supplier_part_id": "s", "quantity": 2, "unit_cost": "10.00", "currency": "ILS"}]}
    body = so.render_purchase_order(row, {"s": {"supplier_sku": "X1"}},
                                    {"full_name": "A", "street": "S", "city": "Haifa", "country": "IL"})["body"]
    assert "approval reference 0123456789ab" in body and "X1 x 2 at 10.00 ILS" in body
    assert "no shipping charge has been approved" in body and "directly to our customer" in body
    row["terms"]["shipping"] = {"cost": "25.00", "currency": "ILS"}
    assert "Shipping: 25.00 ILS (approved" in so.render_purchase_order(row, {"s": {}}, {})["body"]


async def test_manual_order_record_needs_po_approval_and_never_pays():
    store, auth = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()
    r = (await pr.prepare(store, order_id="o9", order_number="9", supplier_id="m", items=ITEMS, shipping={},
                          checks={}))["procurement"]
    with pytest.raises(au.AuthorizationError):
        await pr.record_manual_submission(store, auth, r["id"], "REF-1", approver="owner")
    await pr.authorize_po(store, auth, r["id"], approver="owner", source="test")
    await pr.record_manual_submission(store, auth, r["id"], "REF-1", approver="owner")
    assert store.rows[r["id"]]["state"] == "SUBMITTED" and not store.rows[r["id"]].get("payment_authorization_id")
    assert [a.domain for a in auth.rows] == ["purchase_order"]


def test_awaiting_credentials_slot_is_not_operational():
    c = so.connector_for({"name": "ASAP Network", "credentials": {"status": "pending_credentials",
                                                                  "needs": "ASAP account token"}})
    assert c.mode == "awaiting_credentials" and not getattr(c, "can_pay", False)


async def test_awaiting_credentials_slot_refuses_orders():
    c = so.AwaitingCredentialsConnector("ASAP account token")
    res = await c.place_order({"id": "x"})
    assert not res.success and "ASAP account token" in res.error_message


# ═══ TODO 8: simulated end-to-end with dropshipping + AliExpress ═══════════════════════════════════
async def test_simulated_end_to_end_dropship_aliexpress(monkeypatch):
    """SIMULATED: fake AliExpress, no real Gmail, no real payment provider."""
    monkeypatch.setenv("SUPPLIER_AUTO_ORDER_ENABLED", "1")
    # 1-2. discovery + evidence; dropshipping determined from evidence, not assumed
    rec = q.from_sourcing_signals("ae", {"ships_il": True, "has_api": True}, "aliexpress.com")
    assert q.classify(rec, NOW)["dropshipping"] == q.DROPSHIP_CANDIDATE
    # 8. customer payment -> preparation only
    store, auth, fake = pr.InMemoryProcurementStore(), au.InMemoryAuthStore(), FakeAE()
    terms = pr.material_terms("o1", "ae", ITEMS, {"cost": None, "currency": "ILS"})

    async def terms_for(order, items):
        return {"ae": {"items": ITEMS, "shipping": {"cost": None}, "checks": {}, "terms": terms}}

    monkeypatch.setattr(pr, "current_terms_for_order", terms_for)
    order = SimpleNamespace(id="o1", order_number="A-1")
    assert await pr.gate_paid_order(order, [], store=store, auth_store=auth)
    row = next(iter(store.rows.values()))
    conn = ae_connector(fake)
    # negative: paid shipping, stale price, unavailable variant -> no order
    for bad in (FakeAE(freight=[{"code": "P", "tracking": "true", "free_shipping": "false", "shipping_fee_cent": "3"}]),
                FakeAE(skus=[{"sku_id": "1", "offer_sale_price": "20.00", "currency_code": "USD", "sku_available_stock": 5}]),
                FakeAE(skus=[{"sku_id": "1", "offer_sale_price": "14.24", "currency_code": "USD", "sku_available_stock": 0}])):
        s2, a2 = pr.InMemoryProcurementStore(), au.InMemoryAuthStore()
        r2 = (await pr.prepare(s2, order_id="o1", order_number="A", supplier_id="ae", items=ITEMS,
                               shipping={"cost": None, "currency": "ILS"}, checks={}))["procurement"]
        await pr.authorize_po(s2, a2, r2["id"], approver="owner", source="test")
        assert not (await pr.submit_po(s2, a2, r2["id"], ae_connector(bad), terms))["submitted"]
        assert "aliexpress.ds.order.create" not in bad.methods()
    # 9. order approval -> one unpaid order
    await pr.authorize_po(store, auth, row["id"], approver="owner", source="test")
    assert (await pr.submit_po(store, auth, row["id"], conn, terms))["submitted"]
    assert "aliexpress.ds.order.afterpay" not in fake.methods()
    # 12. replays: duplicate webhook, repeated approval command, repeated submit
    assert await pr.gate_paid_order(order, [], store=store, auth_store=auth)
    assert not (await pr.submit_po(store, auth, row["id"], conn, terms))["submitted"]
    # 10. payment approval -> paid once
    assert not (await pr.pay_via_connector(store, auth, row["id"], conn, terms))["paid"]
    await pr.authorize_payment(store, auth, row["id"], approver="owner", source="test")
    assert (await pr.pay_via_connector(store, auth, row["id"], conn, terms))["paid"]
    assert not (await pr.pay_via_connector(store, auth, row["id"], conn, terms))["paid"]
    assert "no second payment" in await pr.gate_paid_order(order, [], store=store, auth_store=auth)
    # 11. status is read-only
    n = len(fake.calls)
    await pr.reconcile(store, row["id"], conn)
    assert {m for m, _ in fake.calls[n:]} <= {"aliexpress.trade.ds.order.get", "aliexpress.ds.order.tracking.get"}
    assert fake.methods().count("aliexpress.ds.order.create") == 1 and fake.methods().count("aliexpress.ds.order.afterpay") == 1


# the five dropship suppliers exactly as stored live (read-only query 2026-10-27; ids/needs abbreviated)
LIVE_SLOTS = [("Meyer Distributing", "pending_credentials"), ("American Tire Distributors (ATD)", "pending_credentials"),
              ("ASAP Network", "account_active_pending_brand_approval"),
              ("Keystone Automotive Operations", "application_submitted_pending_review"),
              ("Turn 14 Distribution", "pending_review")]


@pytest.mark.parametrize("name,status", LIVE_SLOTS)
@pytest.mark.parametrize("auto", [None, "1"])
async def test_each_live_dropship_slot_is_not_orderable(monkeypatch, name, status, auto):
    if auto:
        monkeypatch.setenv("SUPPLIER_AUTO_ORDER_ENABLED", auto)
    else:
        monkeypatch.delenv("SUPPLIER_AUTO_ORDER_ENABLED", raising=False)
    c = so.connector_for({"name": name, "is_active": False, "credentials": {"status": status, "needs": "Owner: apply"}})
    assert c.mode == "awaiting_credentials"
    assert not (await c.place_order({"id": "x"})).success


@pytest.mark.parametrize("auto", [None, "1"])
def test_inactive_supplier_is_never_orderable(monkeypatch, auto):
    if auto:
        monkeypatch.setenv("SUPPLIER_AUTO_ORDER_ENABLED", auto)
    for sup in ({"name": "AliExpress", "is_active": False}, {"name": "AliExpress"}, {"name": "X", "is_active": None}):
        assert so.connector_for(sup).mode == "manual"


# ── 2026-10-28: one-shot approvals are consumed ATOMICALLY ───────────────────────────────────────────────────
class _YieldingAuthStore(au.InMemoryAuthStore):
    """Returns SNAPSHOTS (like a DB read) and yields to the event loop after the read, so concurrent callers
    interleave like real round trips: every caller can see the grant unconsumed before anyone consumes it."""

    async def candidates(self, *a, **k):
        import dataclasses
        rows = [dataclasses.replace(x) for x in await super().candidates(*a, **k)]
        await asyncio.sleep(0)
        return rows


async def test_one_payment_approval_releases_fulfillment_once_under_concurrency():
    store, auth = pr.InMemoryProcurementStore(), _YieldingAuthStore()
    ship = {"cost": "0", "currency": "ILS"}
    row = (await pr.prepare(store, order_id="o-race", order_number="R", supplier_id="s1", items=ITEMS,
                            shipping=ship, checks={}))["procurement"]
    terms = pr.material_terms("o-race", "s1", ITEMS, ship)
    await pr.authorize_po(store, auth, row["id"], approver="owner", source="test")
    await pr.record_manual_submission(store, auth, row["id"], "SUP-1", approver="owner")
    await pr.authorize_payment(store, auth, row["id"], approver="owner", source="test")
    results = await asyncio.gather(*[pr.payment_refusal(store, auth, "o-race", {"s1": terms}) for _ in range(5)])
    assert sum(r is None for r in results) == 1                       # before the fix: 5 of 5 were released
    assert all("already used" in r or "different terms" in r for r in results if r is not None)


async def test_consume_reports_who_won_and_never_reconsumes():
    auth = au.InMemoryAuthStore()
    a = await au.grant(auth, domain="supplier_payment", scope="pay_supplier", entity_type="procurement",
                       entity_id="p1", approved_by="owner", source="test", terms={"t": 1})
    b = await au.require(auth, "supplier_payment", "procurement", "p1", scope="pay_supplier", terms={"t": 1})
    assert await au.consume(auth, a, "first") is True
    assert await au.consume(auth, b, "second") is False               # a second holder of the same grant loses
    assert auth.rows[0].result == "first"
