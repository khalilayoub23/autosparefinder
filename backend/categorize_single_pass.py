#!/usr/bin/env python3
"""
Single-pass CASE WHEN categorization — one table scan to categorize all
2.5M כללי/accessories parts using name + name_he keyword matching.
"""
import asyncio, asyncpg, os, time

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")

# Build one big CASE WHEN SQL. Order matters — first match wins.
# Format: (category, [(column, pattern), ...])
RULES = [
    ("safety-systems",   [("name","airbag"),("name","air bag"),("name","seat belt"),("name","seatbelt"),("name","pretensioner"),
                          ("name_he","כרית אוויר"),("name_he","חגורת בטיחות")]),
    ("brakes",           [("name","brake"),("name","caliper"),("name","rotor"),("name","abs sensor"),
                          ("name_he","בלם"),("name_he","דיסק בלם"),("name_he","רפידות"),("name_he","ממסר בלם")]),
    ("engine",           [("name","piston"),("name","head gasket"),("name","crankshaft"),("name","camshaft"),
                          ("name","spark plug"),("name","valve cover"),("name","oil pump"),("name","timing chain"),
                          ("name","engine mount"),("name","engine oil"),
                          ("name_he","מנוע"),("name_he","בוכנה"),("name_he","גל ארכובה"),("name_he","מצת"),("name_he","שסתום")]),
    ("fuel-air",         [("name","fuel pump"),("name","fuel filter"),("name","injector"),("name","fuel rail"),
                          ("name","air filter"),("name","throttle body"),("name","mass air"),("name","intake manifold"),
                          ("name_he","משאבת דלק"),("name_he","מסנן דלק"),("name_he","מזרק"),("name_he","מסנן אוויר")]),
    ("air-conditioning-heating",[("name","air conditioning"),("name","compressor"),("name","evaporator"),
                                  ("name","condenser"),("name","heater core"),("name","blower"),("name","hvac"),
                                  ("name_he","מזגן"),("name_he","מדחס"),("name_he","אידוי"),("name_he","חימום")]),
    ("cooling",          [("name","radiator"),("name","water pump"),("name","coolant"),("name","thermostat"),
                          ("name","cooling fan"),("name","intercooler"),("name","expansion tank"),
                          ("name_he","רדיאטור"),("name_he","משאבת מים"),("name_he","מאוורר"),("name_he","תרמוסטט")]),
    ("exhaust",          [("name","exhaust"),("name","muffler"),("name","catalytic"),("name","dpf"),("name","egr"),
                          ("name","lambda"),("name","oxygen sensor"),
                          ("name_he","פליטה"),("name_he","מאיין"),("name_he","ממיר")]),
    ("electrical-sensors",[("name","sensor"),("name","switch"),("name","relay"),("name","fuse"),("name","control unit"),
                            ("name","module"),("name","ecu"),("name","abs module"),("name","wire harness"),
                            ("name_he","חיישן"),("name_he","ממסר"),("name_he","נתיך"),("name_he","בקרה")]),
    ("lighting",         [("name","headlight"),("name","tail light"),("name","fog light"),("name","bulb"),
                          ("name","led"),("name","reflector"),("name","turn signal"),("name","daytime running"),
                          ("name_he","פנס"),("name_he","תאורה"),("name_he","נורה"),("name_he","רפלקטור"),("name_he","לד")]),
    ("wipers-washers",   [("name","wiper"),("name","washer"),("name","windshield washer"),
                          ("name_he","ממחק"),("name_he","מגב"),("name_he","שפריצר")]),
    ("suspension-steering",[("name","shock absorber"),("name","strut"),("name","control arm"),("name","tie rod"),
                             ("name","ball joint"),("name","steering rack"),("name","stabilizer"),("name","sway bar"),
                             ("name","spring coil"),
                             ("name_he","בולם"),("name_he","קפיץ"),("name_he","זרוע"),("name_he","הגה"),("name_he","מוט")]),
    ("wheels-bearings",  [("name","wheel bearing"),("name","hub bearing"),("name","wheel hub"),("name","cv joint"),
                          ("name","drive shaft"),("name","axle"),
                          ("name_he","מיסב"),("name_he","נבה"),("name_he","ציר הנעה")]),
    ("clutch-drivetrain",[("name","clutch"),("name","flywheel"),("name","pressure plate"),("name","release bearing"),
                          ("name","differential"),("name","propshaft"),
                          ("name_he","מצמד"),("name_he","קלאץ"),("name_he","גלגל תנופה")]),
    ("gearbox",          [("name","gearbox"),("name","transmission"),("name","gear shift"),("name","synchronizer"),
                          ("name_he","תיבת הילוכים"),("name_he","גיר")]),
    ("belts-chains",     [("name","timing belt"),("name","serpentine belt"),("name","v-belt"),("name","belt kit"),
                          ("name","timing chain"),("name","tensioner"),("name","idler pulley"),
                          ("name_he","רצועה"),("name_he","גלגלת"),("name_he","שרשרת")]),
    ("interior-comfort", [("name","seat"),("name","dashboard"),("name","door handle"),("name","armrest"),
                          ("name","headrest"),("name","sun visor"),("name","trim panel"),
                          ("name_he","מושב"),("name_he","ריפוד"),("name_he","לוח מחוונים"),("name_he","ידית")]),
    ("body-exterior",    [("name","bumper"),("name","fender"),("name","hood"),("name","grille"),("name","spoiler"),
                          ("name","windshield"),("name","rear window"),("name","side mirror"),("name","door panel"),
                          ("name","mud flap"),("name","splash guard"),
                          ("name_he","פגוש"),("name_he","כנף"),("name_he","בונט"),("name_he","גריל"),
                          ("name_he","שמשה"),("name_he","ראי"),("name_he","ספוילר"),("name_he","חלון")]),
]


def build_when(col, pattern):
    escaped = pattern.replace("'", "''")
    return f"{col} ILIKE '%{escaped}%'"


async def main():
    conn = await asyncpg.connect(DB, statement_cache_size=0)
    t0 = time.monotonic()

    count = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE is_active AND category IN ('כללי','accessories')"
    )
    print(f"[cat1pass] {count:,} uncategorized parts to process", flush=True)

    # Build CASE WHEN expression
    case_parts = []
    for cat, conditions in RULES:
        when_conds = " OR ".join(build_when(col, pat) for col, pat in conditions)
        case_parts.append(f"        WHEN ({when_conds}) THEN '{cat}'")

    case_sql = "CASE\n" + "\n".join(case_parts) + "\n        ELSE category\n    END"

    sql = f"""
        UPDATE parts_catalog
        SET category   = {case_sql},
            updated_at = NOW()
        WHERE is_active
          AND category IN ('כללי', 'accessories')
    """

    print("[cat1pass] Running single-pass categorization...", flush=True)
    try:
        r = await conn.execute(sql)
        n = int(r.split()[-1])
        elapsed = time.monotonic() - t0
        print(f"[cat1pass] Updated {n:,} rows in {elapsed:.0f}s", flush=True)
    except Exception as e:
        print(f"[cat1pass] ERROR: {e}", flush=True)
        await conn.close()
        return

    # Show results
    rows = await conn.fetch(
        "SELECT category, COUNT(*) n FROM parts_catalog WHERE is_active GROUP BY 1 ORDER BY n DESC LIMIT 25"
    )
    print("\nFinal category breakdown:", flush=True)
    for r in rows:
        print(f"  {r['category']:<35} {r['n']:>10,}", flush=True)

    remaining = await conn.fetchval(
        "SELECT COUNT(*) FROM parts_catalog WHERE is_active AND category IN ('כללי','accessories')"
    )
    elapsed = time.monotonic() - t0
    print(f"\n[cat1pass] Done. Remaining uncategorized: {remaining:,} ({elapsed:.0f}s total)", flush=True)
    await conn.close()


asyncio.run(main())
