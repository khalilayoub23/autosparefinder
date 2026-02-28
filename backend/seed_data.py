"""
Seed script: suppliers, parts catalog, supplier_parts
Run: python seed_data.py
"""
import asyncio
import uuid
from decimal import Decimal
from dotenv import load_dotenv
load_dotenv()

from BACKEND_DATABASE_MODELS import (
    engine, Base, async_session_factory,
    Supplier, SupplierPart, PartsCatalog, Vehicle,
)

# ---------------------------------------------------------------------------
SUPPLIERS = [
    {"name": "AutoParts Pro IL", "country": "Israel", "website": "https://autopartspro.co.il", "priority": 1, "reliability_score": 4.8},
    {"name": "Global Parts Hub", "country": "Germany",  "website": "https://globalpartshub.de",  "priority": 2, "reliability_score": 4.5},
    {"name": "EastAuto Supply",  "country": "China",    "website": "https://eastauto.cn",          "priority": 3, "reliability_score": 3.9},
]

PARTS = [
    # Engine
    ("פילטר שמן - טויוטה קורולה 2015-2023",     "Toyota",    "מנוע",       "aftermarket", "OIL-TOY-001",  "פילטר שמן מנוע איכותי מתאים לטויוטה קורולה",  12.50),
    ("פילטר אוויר - מאזדה 3 2017-2023",          "Mazda",     "מנוע",       "aftermarket", "AIR-MAZ-001",  "פילטר אוויר בצריכת דלק מינימלית",              8.20),
    ("פילטר שמן - יונדאי i35 2015-2022",         "Hyundai",   "מנוע",       "OEM",         "OIL-HYN-001",  "פילטר שמן OEM מקורי יונדאי",                   14.00),
    ("פילטר דלק - קיה ספורטאז' 2016-2022",       "Kia",       "מנוע",       "aftermarket", "FUEL-KIA-001", "פילטר דלק מומלץ לרכבי קיה",                    9.75),
    ("חגורת שיניים - פולקסווגן גולף 2014-2020", "VW",        "מנוע",       "OEM",         "BELT-VW-001",  "חגורת שיניים מקורית, כולל גלגלת מתיחה",        85.00),
    ("מכסה תפסיל שמן - מאזדה 6",                 "Mazda",     "מנוע",       "aftermarket", "CAP-MAZ-001",  "מכסה תפסיל שמן עם אטם סיליקון",                 4.50),
    ("גסקט ראש מנוע - טויוטה אוונסיס",           "Toyota",    "מנוע",       "OEM",         "GASK-TOY-001", "גסקט ראש מנוע מקורי טויוטה OEM",               45.00),
    ("שרשרת תזמון - BMW 320i 2012-2019",          "BMW",       "מנוע",       "OEM",         "CHAIN-BMW-001","שרשרת תזמון מקורית BMW עם ערכת טנשנר",        120.00),

    # Brakes
    ("רפידות בלם קדמי - טויוטה קורולה",          "Toyota",    "בלמים",      "aftermarket", "BRAKE-TOY-F1", "רפידות בלם קדמי, ER800 ceramic איכותיות",      22.00),
    ("רפידות בלם קדמי - מאזדה 3",                "Mazda",     "בלמים",      "aftermarket", "BRAKE-MAZ-F1", "רפידות בלם קדמי מאזדה 3 2013+",                24.50),
    ("רפידות בלם קדמי - יונדאי טוסון",           "Hyundai",   "בלמים",      "OEM",         "BRAKE-HYN-F1", "רפידות בלם קדמי מקוריות יונדאי",               35.00),
    ("דיסקיות בלם קדמי - קיה ספורטאז'",          "Kia",       "בלמים",      "aftermarket", "DISC-KIA-F1",  "זוג דיסקיות בלם קדמי, מחוונות שחיקה",          55.00),
    ("דיסקיות בלם קדמי - פולקסווגן פאסאט",       "VW",        "בלמים",      "OEM",         "DISC-VW-F1",   "דיסקיות בלם מקוריות פולקסווגן",                60.00),
    ("קליפר בלם אחורי - סובארו אימפרזה",         "Subaru",    "בלמים",      "aftermarket", "CALIP-SUB-R1", "קליפר בלם אחורי מחודש",                         75.00),
    ("נוזל בלמים DOT4 - 0.5L",                   "Universal", "בלמים",      "aftermarket", "FLUID-DOT4",   "נוזל בלמים DOT4 איכותי לכל הרכבים",             6.80),

    # Suspension
    ("מוט הגה - טויוטה יאריס 2011-2020",         "Toyota",    "היגוי ומתלים","aftermarket", "TIE-TOY-001",  "מוט הגה פנימי כולל חיבור",                     18.00),
    ("כרית אמורטיזציה קדמית - מאזדה 6",          "Mazda",     "היגוי ומתלים","OEM",         "STRT-MAZ-001", "כרית אמורטיזציה קדמית מקורית מאזדה",           28.00),
    ("אמורטיזאטור קדמי - יונדאי אלנטרה",         "Hyundai",   "היגוי ומתלים","aftermarket", "SHOCK-HYN-F1", "אמורטיזאטור קדמי כולל קפיץ",                   90.00),
    ("סרן עליון - BMW X5 2008-2013",              "BMW",       "היגוי ומתלים","OEM",         "ARM-BMW-001",  "סרן עליון מקורי BMW",                           110.00),
    ("מיסב גלגל קדמי - ניסאן קשקאי",             "Nissan",    "היגוי ומתלים","aftermarket", "BEAR-NIS-F1",  "מיסב גלגל קדמי עם ABS",                         38.00),
    ("מייצב פנימי קדמי - שקדה אוקטביה",          "Skoda",     "היגוי ומתלים","aftermarket", "SWAY-SKO-F1",  "מייצב אנטי-רול קדמי",                           15.00),

    # Electrical
    ("מצבר 12V 60Ah - Universal",                "Universal", "חשמל",       "aftermarket", "BAT-60AH",     "מצבר רכב 12V 60Ah מגבר 550A CCA",               85.00),
    ("מצבר 12V 74Ah - Universal",                "Universal", "חשמל",       "aftermarket", "BAT-74AH",     "מצבר רכב 12V 74Ah מגבר 680A CCA",               95.00),
    ("פמס אוזן הגה - הונדה ציוויק 2012-2017",    "Honda",     "חשמל",       "OEM",         "LAMP-HON-F1",  "פנס ראשי מקורי הונדה, ימין",                   145.00),
    ("מגנטו - ניסאן אלטימה 2012-2018",           "Nissan",    "חשמל",       "aftermarket", "ALT-NIS-001",  "מגנטו מחודש ניסאן אלטימה",                      95.00),
    ("סטרטר - מיצובישי לנסר",                    "Mitsubishi","חשמל",       "aftermarket", "START-MIT-001","סטרטר מחודש מיצובישי לנסר",                     68.00),
    ("חיישן חמצן - טויוטה קמרי",                 "Toyota",    "חשמל",       "OEM",         "O2-TOY-001",   "חיישן חמצן מקורי, Upstream",                    42.00),
    ("ממסר דלק - מאזדה CX-5",                    "Mazda",     "חשמל",       "aftermarket", "PUMP-MAZ-001", "משאבת דלק כולל מכלול",                           88.00),

    # Cooling
    ("תרמוסטט - יונדאי i20 2014-2020",           "Hyundai",   "קירור",      "OEM",         "THERM-HYN-001","תרמוסטט מקורי יונדאי 88°C",                     22.00),
    ("מצנן - קיה ריו 2011-2017",                 "Kia",       "קירור",      "aftermarket", "RAD-KIA-001",  "מצנן אלומיניום כולל מאווררים",                  165.00),
    ("פקעת מים - פולקסווגן פולו",                "VW",        "קירור",      "OEM",         "WP-VW-001",    "פקעת מים מקורית פולקסווגן",                      48.00),
    ("מאוורר קירור - ניסאן מיקרה",               "Nissan",    "קירור",      "aftermarket", "FAN-NIS-001",  "מודול מאוורר קירור כפול",                        75.00),

    # Tyres & Wheels
    ("צמיג 205/55R16 - Michelin",                "Michelin",  "צמיגים",     "aftermarket", "TYR-MICH-001", "צמיג Michelin Energy Saver+, 205/55R16",         85.00),
    ("צמיג 195/65R15 - Bridgestone",             "Bridgestone","צמיגים",    "aftermarket", "TYR-BRDG-001", "צמיג Bridgestone Ecopia, 195/65R15",             72.00),
    ("גלגל - Ford Focus 2011-2018",               "Ford",      "גלגלים",     "aftermarket", "WHEEL-FOR-001","גלגל פלדה 16\" R-Type",                           45.00),
    ("כיסוי גלגל - Universal 15\"",              "Universal", "גלגלים",     "aftermarket", "HUB-UNI-15",   "4 כיסויי גלגל 15\" שחור",                        18.00),

    # Transmission
    ("שמן גיר אוטומטי ATF - 1L",                "Universal", "תיבת הילוכים","aftermarket","ATF-1L",        "שמן גיר אוטומטי ATF Dexron III, 1 ליטר",         8.50),
    ("דיסק מצמד - פיג'ו 308 2010-2018",          "Peugeot",   "תיבת הילוכים","aftermarket","CLUTCH-PEU-001","דיסק מצמד + גלגל תנופה",                        95.00),

    # Exhaust
    ("סיר פליטה - טויוטה קורולה 2014-2019",      "Toyota",    "פליטה",      "aftermarket", "EXH-TOY-001",  "סיר פליטה אחורי נירוסטה",                        65.00),
    ("קטליזטור - יונדאי אקסנט",                  "Hyundai",   "פליטה",      "OEM",         "CAT-HYN-001",  "קטליזטור מקורי, Euro 5",                         280.00),

    # Wipers & Cabin
    ("מגבי שמשות קדמי + אחורי - Universal",     "Bosch",     "מגבים",      "aftermarket", "WIPER-BCH-001","סט מגבים Bosch Aerotwin A863S",                  24.00),
    ("פילטר מזגן - Universal",                    "Universal", "מזגן",       "aftermarket", "CABIN-UNI-001","פילטר אוויר פנים/מזגן, HEPA",                    12.00),
    ("נוזל שמשות קרה -20C - 1L",                 "Universal", "מכניקה כללי","aftermarket", "WASH-NEG20",   "נוזל שמשות אנטי-קפאון -20°C, 1 ליטר",            4.00),
]


async def main():
    async with async_session_factory() as db:
        # --- Suppliers ---
        created_suppliers = []
        for s in SUPPLIERS:
            sup = Supplier(
                id=uuid.uuid4(), name=s["name"], country=s["country"],
                website=s["website"], priority=s["priority"],
                reliability_score=s["reliability_score"], is_active=True,
            )
            db.add(sup)
            created_suppliers.append(sup)
        await db.flush()
        print(f"[+] Created {len(created_suppliers)} suppliers")

        # --- Parts catalog ---
        created_parts = []
        for name, mfr, cat, ptype, sku, desc, usd_price in PARTS:
            part = PartsCatalog(
                id=uuid.uuid4(), name=name, manufacturer=mfr,
                category=cat, part_type=ptype, sku=sku,
                description=desc, is_active=True,
                specifications={"weight_kg": 0.5},
            )
            db.add(part)
            created_parts.append((part, usd_price))
        await db.flush()
        print(f"[+] Created {len(created_parts)} parts")

        # --- Supplier parts (every part on all suppliers with varying prices) ---
        sp_count = 0
        for part, base_usd in created_parts:
            for i, sup in enumerate(created_suppliers):
                multiplier = 1.0 + i * 0.08  # 2nd supplier 8% more, 3rd 16%
                sp = SupplierPart(
                    id=uuid.uuid4(),
                    part_id=part.id,
                    supplier_id=sup.id,
                    supplier_sku=f"{part.sku}-{sup.name[:3].upper()}",
                    price_usd=round(base_usd * multiplier, 2),
                    price_ils=None,
                    is_available=True,
                    warranty_months=12,
                    estimated_delivery_days=7 + i * 3,
                )
                db.add(sp)
                sp_count += 1

        await db.commit()
        print(f"[+] Created {sp_count} supplier part entries")
        print("\n✅ Seeding complete!")
        print(f"   {len(created_suppliers)} suppliers")
        print(f"   {len(created_parts)} catalog parts")
        print(f"   {sp_count} supplier parts")


if __name__ == "__main__":
    asyncio.run(main())
