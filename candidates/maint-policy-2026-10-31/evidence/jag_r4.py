import asyncio, os, sys
sys.path.insert(0, "/app")
import asyncpg, db_update_agent as d
async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"),
                              server_settings={"default_transaction_read_only": "on"})
    rows = await c.fetch("""SELECT pvf.id::text, pvf.model, pc.name, pc.name_he FROM part_vehicle_fitment pvf
        JOIN parts_catalog pc ON pc.id = pvf.part_id WHERE pvf.notes = 'name_parse:jaguar' ORDER BY pc.name""")
    bad = []
    for r in rows:
        found, why = d._jaguar_fitment_from_name(r["name"] or "", r["name_he"] or "")
        keep = r["model"] in {f[1] for f in found}
        print("KEEP" if keep else f"DROP({why})", "|", r["model"], "|", r["name"])
        if not keep: bad.append(r["id"])
    open("/m/jaguar_r4_ids.txt", "w").write("\n".join(bad) + "\n")
    print("total", len(rows), "drop", len(bad))
    await c.close()
asyncio.run(main())
