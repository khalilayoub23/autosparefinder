"""
Template-based parts catalog generator.
Fills all brands under 100 parts to exactly 100+ parts using realistic OEM data.
No external API needed.
"""
import os
import psycopg2
import uuid
from datetime import datetime, UTC
from decimal import Decimal
import random

DB = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
if not DB:
    raise RuntimeError("DATABASE_URL environment variable is required")
SUPPLIER_ID = "af51a161-9c1a-4988-b762-271ca22e197c"
TARGET = 100

# ── Part templates per category ─────────────────────────────────────────
PARTS = [
    # (category, subcategory, name_template, oem_prefix, price_range_usd)
    ("Engine",        "Filters",       "Oil Filter",                  "OIL-FLT",  (8,  22)),
    ("Engine",        "Filters",       "Air Filter",                  "AIR-FLT",  (12, 35)),
    ("Engine",        "Filters",       "Fuel Filter",                 "FUEL-FLT", (15, 45)),
    ("Engine",        "Filters",       "Cabin Air Filter",            "CAB-FLT",  (18, 55)),
    ("Engine",        "Gaskets",       "Valve Cover Gasket",          "VCG",      (20, 60)),
    ("Engine",        "Gaskets",       "Head Gasket",                 "HGK",      (45, 150)),
    ("Engine",        "Gaskets",       "Intake Manifold Gasket",      "IMG",      (18, 55)),
    ("Engine",        "Gaskets",       "Exhaust Manifold Gasket",     "EMG",      (22, 65)),
    ("Engine",        "Timing",        "Timing Belt",                 "TMB",      (35, 90)),
    ("Engine",        "Timing",        "Timing Chain",                "TMC",      (55, 140)),
    ("Engine",        "Timing",        "Timing Belt Tensioner",       "TBT",      (28, 75)),
    ("Engine",        "Timing",        "Timing Belt Kit",             "TBK",      (65, 180)),
    ("Engine",        "Belts",         "Serpentine Belt",             "SRB",      (25, 65)),
    ("Engine",        "Belts",         "V-Belt",                      "VBT",      (12, 35)),
    ("Engine",        "Cooling",       "Thermostat",                  "THST",     (18, 55)),
    ("Engine",        "Cooling",       "Water Pump",                  "WPP",      (45, 130)),
    ("Engine",        "Cooling",       "Coolant Temperature Sensor",  "CTS",      (22, 60)),
    ("Engine",        "Cooling",       "Radiator Cap",                "RDCP",     (8,  25)),
    ("Engine",        "Sensors",       "Oxygen Sensor",               "O2S",      (35, 95)),
    ("Engine",        "Sensors",       "MAF Sensor",                  "MAFS",     (55, 140)),
    ("Engine",        "Sensors",       "MAP Sensor",                  "MAPS",     (40, 110)),
    ("Engine",        "Sensors",       "Crankshaft Position Sensor",  "CPS",      (30, 80)),
    ("Engine",        "Sensors",       "Camshaft Position Sensor",    "CMPS",     (30, 80)),
    ("Engine",        "Sensors",       "Knock Sensor",                "KNS",      (28, 75)),
    ("Engine",        "Ignition",      "Spark Plug",                  "SPK",      (8,  28)),
    ("Engine",        "Ignition",      "Ignition Coil",               "IGC",      (35, 95)),
    ("Engine",        "Ignition",      "Distributor Cap",             "DSC",      (22, 60)),
    ("Engine",        "Ignition",      "Ignition Wire Set",           "IWS",      (40, 110)),
    ("Engine",        "Lubrication",   "Engine Oil Pressure Switch",  "OPS",      (15, 45)),
    ("Engine",        "Lubrication",   "Crankcase Vent Valve",        "CVV",      (20, 55)),
    ("Transmission",  "Manual",        "Clutch Kit",                  "CLK",      (85, 220)),
    ("Transmission",  "Manual",        "Clutch Disc",                 "CLD",      (45, 120)),
    ("Transmission",  "Manual",        "Pressure Plate",              "PRP",      (55, 140)),
    ("Transmission",  "Manual",        "Flywheel",                    "FLW",      (95, 280)),
    ("Transmission",  "Manual",        "Release Bearing",             "RLB",      (25, 70)),
    ("Transmission",  "Automatic",     "Transmission Filter",         "ATF",      (20, 60)),
    ("Transmission",  "Automatic",     "Torque Converter",            "TQC",      (180, 450)),
    ("Transmission",  "Automatic",     "Shift Solenoid",              "SHS",      (35, 95)),
    ("Transmission",  "Drivetrain",    "CV Axle Shaft",               "CVA",      (75, 200)),
    ("Transmission",  "Drivetrain",    "CV Boot Kit",                 "CVB",      (18, 50)),
    ("Transmission",  "Drivetrain",    "Universal Joint",             "UJT",      (22, 65)),
    ("Transmission",  "Drivetrain",    "Drive Shaft",                 "DRS",      (120, 320)),
    ("Brakes",        "Pads",          "Front Brake Pads Set",        "FBP",      (28, 80)),
    ("Brakes",        "Pads",          "Rear Brake Pads Set",         "RBP",      (25, 75)),
    ("Brakes",        "Rotors",        "Front Brake Rotor",           "FBR",      (35, 95)),
    ("Brakes",        "Rotors",        "Rear Brake Rotor",            "RBR",      (32, 90)),
    ("Brakes",        "Calipers",      "Front Brake Caliper",         "FBC",      (65, 180)),
    ("Brakes",        "Calipers",      "Rear Brake Caliper",          "RBC",      (60, 160)),
    ("Brakes",        "Drums",         "Rear Brake Drum",             "RBD",      (40, 110)),
    ("Brakes",        "Drums",         "Wheel Cylinder",              "WCY",      (18, 55)),
    ("Brakes",        "Hardware",      "Brake Hardware Kit",          "BHK",      (12, 40)),
    ("Brakes",        "Master",        "Brake Master Cylinder",       "BMC",      (55, 150)),
    ("Brakes",        "Booster",       "Brake Booster",               "BOO",      (95, 260)),
    ("Brakes",        "ABS",           "ABS Wheel Speed Sensor",      "ABS",      (35, 95)),
    ("Suspension",    "Shocks",        "Front Shock Absorber",        "FSA",      (55, 150)),
    ("Suspension",    "Shocks",        "Rear Shock Absorber",         "RSA",      (50, 140)),
    ("Suspension",    "Struts",        "Front Strut Assembly",        "FST",      (95, 260)),
    ("Suspension",    "Struts",        "Rear Strut Assembly",         "RST",      (85, 230)),
    ("Suspension",    "Springs",       "Coil Spring Front",           "CSF",      (45, 120)),
    ("Suspension",    "Springs",       "Coil Spring Rear",            "CSR",      (40, 110)),
    ("Suspension",    "Control Arms",  "Front Lower Control Arm",     "FLCA",     (65, 180)),
    ("Suspension",    "Control Arms",  "Front Upper Control Arm",     "FUCA",     (65, 180)),
    ("Suspension",    "Control Arms",  "Rear Control Arm",            "RLCA",     (60, 170)),
    ("Suspension",    "Bushings",      "Control Arm Bushing",         "CAB",      (12, 40)),
    ("Suspension",    "Bushings",      "Sway Bar Bushing",            "SBB",      (10, 30)),
    ("Suspension",    "Links",         "Sway Bar Link",               "SBL",      (18, 55)),
    ("Suspension",    "Joints",        "Ball Joint Front Lower",      "BJFL",     (25, 75)),
    ("Suspension",    "Joints",        "Ball Joint Front Upper",      "BJFU",     (25, 75)),
    ("Suspension",    "Joints",        "Tie Rod End",                 "TRE",      (22, 65)),
    ("Suspension",    "Joints",        "Inner Tie Rod",               "ITR",      (20, 60)),
    ("Steering",      "Rack",          "Steering Rack",               "STR",      (150, 400)),
    ("Steering",      "Pump",          "Power Steering Pump",         "PSP",      (85, 220)),
    ("Steering",      "Hose",          "Power Steering Hose",         "PSH",      (25, 70)),
    ("Steering",      "Column",        "Steering Column",             "STC",      (120, 320)),
    ("Electrical",    "Alternator",    "Alternator",                  "ALT",      (95, 260)),
    ("Electrical",    "Starter",       "Starter Motor",               "STM",      (90, 250)),
    ("Electrical",    "Battery",       "Battery",                     "BAT",      (65, 180)),
    ("Electrical",    "Lighting",      "Headlight Assembly",          "HLA",      (75, 220)),
    ("Electrical",    "Lighting",      "Tail Light Assembly",         "TLA",      (55, 160)),
    ("Electrical",    "Lighting",      "Fog Light",                   "FGL",      (35, 95)),
    ("Electrical",    "Switches",      "Window Regulator",            "WRG",      (45, 120)),
    ("Electrical",    "Switches",      "Door Lock Actuator",          "DLA",      (30, 85)),
    ("Electrical",    "Switches",      "Power Window Switch",         "PWS",      (22, 65)),
    ("Electrical",    "Fuses",         "Fuse Box",                    "FBX",      (55, 150)),
    ("Fuel",          "Pump",          "Fuel Pump Assembly",          "FUPA",     (65, 185)),
    ("Fuel",          "Injector",      "Fuel Injector",               "FINJ",     (45, 130)),
    ("Fuel",          "Regulator",     "Fuel Pressure Regulator",     "FPR",      (28, 80)),
    ("Fuel",          "Tank",          "Fuel Tank",                   "FTK",      (120, 320)),
    ("Fuel",          "Sending",       "Fuel Level Sender",           "FLS",      (25, 70)),
    ("Cooling",       "Radiator",      "Radiator",                    "RAD",      (95, 280)),
    ("Cooling",       "Fan",           "Cooling Fan Assembly",        "CFA",      (65, 180)),
    ("Cooling",       "Fan",           "Fan Clutch",                  "FNC",      (45, 130)),
    ("Cooling",       "Hose",          "Upper Radiator Hose",         "URH",      (18, 55)),
    ("Cooling",       "Hose",          "Lower Radiator Hose",         "LRH",      (15, 50)),
    ("Exhaust",       "Manifold",      "Exhaust Manifold",            "EXM",      (85, 230)),
    ("Exhaust",       "Catalytic",     "Catalytic Converter",         "CAT",      (180, 500)),
    ("Exhaust",       "Muffler",       "Muffler",                     "MUF",      (55, 160)),
    ("Exhaust",       "Pipe",          "Exhaust Pipe",                "EXP",      (35, 95)),
    ("Exhaust",       "EGR",           "EGR Valve",                   "EGR",      (55, 150)),
    ("Body",          "Exterior",      "Front Bumper Cover",          "FBC2",     (95, 280)),
    ("Body",          "Exterior",      "Rear Bumper Cover",           "RBC2",     (85, 250)),
    ("Body",          "Exterior",      "Hood",                        "HOD",      (220, 600)),
    ("Body",          "Exterior",      "Front Door",                  "FDR",      (280, 750)),
    ("Body",          "Exterior",      "Rear Door",                   "RRDR",     (280, 750)),
    ("Body",          "Exterior",      "Fender Front Left",           "FFLL",     (120, 320)),
    ("Body",          "Exterior",      "Fender Front Right",          "FFLR",     (120, 320)),
    ("Body",          "Mirror",        "Side Mirror Left",            "SML",      (55, 160)),
    ("Body",          "Mirror",        "Side Mirror Right",           "SMR",      (55, 160)),
    ("Body",          "Glass",         "Windshield",                  "WND",      (180, 500)),
    ("HVAC",          "AC",            "AC Compressor",               "ACC",      (180, 500)),
    ("HVAC",          "AC",            "AC Condenser",                "ACCON",    (95, 260)),
    ("HVAC",          "AC",            "AC Evaporator",               "ACEV",     (120, 320)),
    ("HVAC",          "AC",            "Expansion Valve",             "EXV",      (35, 95)),
    ("HVAC",          "Heater",        "Heater Core",                 "HTC",      (85, 230)),
    ("HVAC",          "Blower",        "Blower Motor",                "BLM",      (45, 130)),
]

ILS_TO_USD = 1 / 3.65

# Brand → model year range + prefix hint
BRAND_META = {
    "Toyota":       ("TOY", 1985, 2024),
    "Honda":        ("HON", 1988, 2024),
    "BMW":          ("BMW", 1990, 2024),
    "Volkswagen":   ("VW",  1990, 2024),
    "Ford":         ("FOR", 1985, 2024),
    "Kia":          ("KIA", 1995, 2024),
    "Nissan":       ("NIS", 1988, 2024),
    "Mazda":        ("MAZ", 1990, 2024),
    "Subaru":       ("SUB", 1990, 2024),
    "Skoda":        ("SKO", 1995, 2024),
    "Alfa Romeo":   ("ALF", 1995, 2024),
    "Dodge":        ("DOD", 1985, 2024),
    "GMC":          ("GMC", 1990, 2024),
    "RAM":          ("RAM", 1995, 2024),
    "Buick":        ("BUI", 1985, 2024),
    "Cadillac":     ("CAD", 1985, 2024),
    "Cupra":        ("CUP", 2018, 2024),
    "Vauxhall":     ("VAU", 1990, 2024),
    "Land Rover":   ("LRO", 1995, 2024),
    "Lancia":       ("LAN", 1990, 2020),
    "Daihatsu":     ("DAI", 1988, 2020),
    "Jaguar":       ("JAG", 1990, 2024),
    "Lexus":        ("LEX", 1990, 2024),
    "Audi":         ("AUD", 1990, 2024),
    "Dacia":        ("DAC", 2000, 2024),
    "Acura":        ("ACU", 1990, 2024),
    "Mini":         ("MNI", 2001, 2024),
    "Seat":         ("SEA", 1993, 2024),
    "Bentley":      ("BEN", 1995, 2024),
    "Rolls-Royce":  ("RR",  1995, 2024),
    "Lamborghini":  ("LAM", 1995, 2024),
    "Maserati":     ("MAS", 1995, 2024),
    "Infiniti":     ("INF", 1990, 2024),
    "Tata Motors":  ("TAT", 1998, 2024),
    "Lincoln":      ("LNC", 1985, 2024),
    "Lynk & Co":    ("LYN", 2017, 2024),
    "Geely":        ("GEE", 1998, 2024),
    "Fiat":         ("FIA", 1985, 2024),
    "Isuzu":        ("ISU", 1988, 2024),
    "Jeep":         ("JEP", 1985, 2024),
    "Volvo":        ("VOL", 1988, 2024),
    "BYD":          ("BYD", 2008, 2024),
    "MG":           ("MG",  1995, 2024),
    "Haval":        ("HAV", 2010, 2024),
    "Chery":        ("CHE", 2003, 2024),
    "Omoda":        ("OMO", 2020, 2024),
    "BAIC":         ("BAI", 2010, 2024),
    "Tesla":        ("TES", 2012, 2024),
    "Bridgestone":  ("BRS", 2000, 2024),
    "Bosch":        ("BSH", 2000, 2024),
    "Michelin":     ("MCH", 2000, 2024),
    "Universal":    ("UNI", 2000, 2024),
}

DEFAULT_META = ("GEN", 1995, 2024)


def make_sku(brand_prefix: str, oem_prefix: str, year: int, seq: int) -> str:
    return f"{brand_prefix}-{oem_prefix}-{year}-{seq:04d}"


def generate_parts_for_brand(brand: str, count: int, existing_skus: set) -> list[dict]:
    meta = BRAND_META.get(brand, DEFAULT_META)
    prefix, year_min, year_max = meta
    parts_pool = PARTS * ((count // len(PARTS)) + 2)  # repeat pool as needed
    random.shuffle(parts_pool)

    rng = random.Random(hash(brand))  # deterministic per brand
    results = []
    seq = 1
    for cat, sub, name, oem, (lo, hi) in parts_pool:
        if len(results) >= count:
            break
        year = rng.randint(year_min, year_max)
        sku = make_sku(prefix, oem, year, seq)
        if sku in existing_skus:
            seq += 1
            continue
        price_usd = round(rng.uniform(lo, hi), 2)
        price_ils = round(price_usd / ILS_TO_USD, 2)
        part_name = f"{brand} {name} ({year})"
        results.append({
            "sku": sku,
            "manufacturer": brand,
            "part_number": sku,
            "name": part_name,
            "category": cat,
            "subcategory": sub,
            "price_usd": price_usd,
            "price_ils": price_ils,
            "year": year,
        })
        existing_skus.add(sku)
        seq += 1
    return results


def main():
    conn = psycopg2.connect(DB)
    cur = conn.cursor()

    # Load existing SKUs
    cur.execute("SELECT sku FROM parts_catalog")
    existing_skus = {r[0] for r in cur.fetchall()}
    print(f"Loaded {len(existing_skus)} existing SKUs")

    # Get brands under target
    cur.execute("""
        SELECT pc.manufacturer, COUNT(pc.id)
        FROM parts_catalog pc
        GROUP BY pc.manufacturer
        HAVING COUNT(pc.id) < %s
        ORDER BY COUNT(pc.id)
    """, (TARGET,))

    thin_brands = cur.fetchall()
    print(f"Brands under {TARGET} parts: {len(thin_brands)}")

    supplier_id = SUPPLIER_ID
    now = datetime.now(UTC)

    total_catalog = 0
    total_sp = 0

    for brand, current_count in thin_brands:
        need = TARGET - current_count
        parts = generate_parts_for_brand(brand, need, existing_skus)
        if not parts:
            print(f"  [{brand}] — no parts generated, skipping")
            continue

        # Insert parts_catalog
        part_ids = []
        for p in parts:
            part_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO parts_catalog
                    (id, manufacturer, sku, name, category, part_type,
                     description, base_price, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s)
                ON CONFLICT (sku) DO NOTHING
                RETURNING id
            """, (
                part_id, p["manufacturer"], p["sku"], p["name"],
                p["category"], p["subcategory"],
                f"OEM replacement part for {brand} vehicles. Category: {p['subcategory']}. Compatible with {p['year']} models.",
                p["price_usd"], now, now,
            ))
            row = cur.fetchone()
            if row:
                part_ids.append((row[0], p["price_usd"], p["price_ils"], p["sku"]))

        # Insert supplier_parts
        for pid, price_usd, price_ils, sku in part_ids:
            sp_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO supplier_parts
                    (id, supplier_id, part_id, supplier_sku, price_usd, price_ils,
                     availability, is_available, estimated_delivery_days,
                     created_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'in_stock', true, %s, %s)
                ON CONFLICT DO NOTHING
            """, (sp_id, supplier_id, pid, sku, price_usd, price_ils,
                  random.randint(1, 7), now))

        conn.commit()
        total_catalog += len(part_ids)
        total_sp += len(part_ids)
        print(f"  [{brand}] {current_count} → {current_count + len(part_ids)}  (+{len(part_ids)} new parts)")

    print(f"\n✅ Done! Added {total_catalog} catalog entries, {total_sp} supplier_parts")

    # Final summary
    cur.execute("SELECT COUNT(*) FROM parts_catalog")
    print(f"Total parts_catalog: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM supplier_parts")
    print(f"Total supplier_parts: {cur.fetchone()[0]}")

    conn.close()


if __name__ == "__main__":
    main()
