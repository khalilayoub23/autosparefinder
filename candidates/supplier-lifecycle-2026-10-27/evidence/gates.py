import asyncio, sys, os
sys.path.insert(0, "/app")
from email_agent.store import PgStore, SupplierSchemaNotReady
STAGE = os.environ["STAGE"]
async def main():
    st = PgStore()
    if STAGE == "old":
        ready = await st.supplier_schema_ready()
        await st.claim("old-schema-1", "t1", 3)
        pend = await st.owner_pending(5); dig = await st.digest_pending(5); op = await st.open_items(5)
        try:
            await st.supplier_route_pending(5); refused = False
        except SupplierSchemaNotReady:
            refused = True
        print(f"CANDIDATE ON 0041: schema_ready={ready} owner_pending/digest/open ok ({len(pend)},{len(dig)},{len(op)}) supplier_routing_refused={refused}")
        return
    import procurement as pr, supplier_authorization as au
    from BACKEND_DATABASE_MODELS import pii_engine
    print(f"CANDIDATE ON 0042: schema_ready={await PgStore().supplier_schema_ready()}")
    p = pr.PgProcurementStore(); a = au.PgAuthStore()
    items = [{"supplier_part_id": "sp", "part_id": "pp", "quantity": 1, "unit_cost": "10", "currency": "ILS"}]
    r = (await pr.prepare(p, order_id="race", order_number="R", supplier_id="s", items=items, shipping={}, checks={}))["procurement"]
    await pr.authorize_po(p, a, r["id"], approver="owner", source="test")
    wins = await asyncio.gather(*[p.claim(r["id"], "PO_AUTHORIZED", "in_flight", {"n": i}) for i in range(10)])
    print(f"RACE submission claims: {sum(wins)} of 10 won")
    await p.update(r["id"], state="SUBMITTED", supplier_reference="X", submission_evidence={"in_flight": False})
    await pr.authorize_payment(p, a, r["id"], approver="owner", source="test")
    wins = await asyncio.gather(*[p.claim(r["id"], "PAYMENT_AUTHORIZED", "pay_in_flight", {"n": i}) for i in range(10)])
    print(f"RACE payment claims: {sum(wins)} of 10 won")
    await pii_engine.dispose()
asyncio.run(main())
