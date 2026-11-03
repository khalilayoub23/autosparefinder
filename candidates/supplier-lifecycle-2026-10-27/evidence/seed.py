# ISOLATED REHEARSAL: write representative data into every object 0042 adds, through the candidate's own code.
import asyncio, sys, uuid, datetime as d
sys.path.insert(0, "/app")
import procurement as pr, supplier_authorization as au, supplier_outreach as so
from email_agent.store import PgStore
from BACKEND_DATABASE_MODELS import pii_engine
async def main():
    a, p, o, es = au.PgAuthStore(), pr.PgProcurementStore(), so.PgOutreachStore(), PgStore()
    await au.grant(a, domain="supplier_message", scope="routine_inquiry", entity_type="policy", entity_id="*",
                   approved_by="owner", source="test", standing=True)
    items = [{"supplier_part_id": "sp-1", "part_id": "p-1", "quantity": 2, "unit_cost": "52.69", "currency": "ILS"}]
    for i in range(3):
        r = (await pr.prepare(p, order_id=f"order-{i}", order_number=f"AUTO-{i}", supplier_id="ae", items=items,
                              shipping={"cost": None, "currency": "ILS"}, checks={"supplier_active": True}))["procurement"]
        if i >= 1:
            await pr.authorize_po(p, a, r["id"], approver="owner", source="test")
        if i == 2:
            await p.claim(r["id"], "PO_AUTHORIZED", "in_flight", {"note": "multi-line\nvalue, with \"quotes\""})
            await pr.record_manual_submission(p, a, r["id"], "AE-8100000001", approver="owner")
            await pr.authorize_payment(p, a, r["id"], approver="owner", source="test")
    row = {"id": str(uuid.uuid4()), "supplier_id": "s1", "recipient": "sales@s.example", "purposes": ["api_info", "dropshipping_terms"],
           "template_version": "inquiry-v1", "state": "READY_TO_CONTACT", "idempotency_key": "k-" + str(uuid.uuid4()),
           "attempt": 1, "parent_id": None, "source_task_id": None}
    await o.insert(row)
    g = (await a.candidates("supplier_message", "policy", "*"))[0]
    await o.update(row["id"], state="AWAITING_REPLY", authorization_id=g.id, sent_at=d.datetime.now(d.timezone.utc),
                   gmail_thread_id="thr-1", gmail_message_id="gm-1")
    from sqlalchemy import text
    from BACKEND_DATABASE_MODELS import pii_session_factory
    async with pii_session_factory() as db:                     # a NEW mail arriving after the migration
        await db.execute(text("INSERT INTO email_agent_messages (gmail_message_id, gmail_thread_id, status, sender_email) "
                              "VALUES ('e3-after-0042', 'thr-1', 'processed', 'sales@s.example')"))
        await db.commit()
    pend = await es.supplier_route_pending(10)
    await es.set_source_folder(pend[0]["gmail_message_id"], "spam")
    await es.set_supplier_route(pend[0]["id"], {"owner": "NIR", "outreach_id": row["id"], "note": "ünïcode ✓"})
    print("SEEDED")
    await pii_engine.dispose()
asyncio.run(main())
