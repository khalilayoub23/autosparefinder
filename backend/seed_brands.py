"""
seed_brands.py
--------------
Inserts/updates the car_brands reference table with every brand visible
in the "Car Companies That Drive The World" chart.

This is REFERENCE DATA only — no fake parts, no fake prices.
The AI agent will use these records to normalize manufacturer names,
match incoming Excel/API data, and suggest completions.

Run:  python seed_brands.py
"""

import asyncio
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

import os
from dotenv import load_dotenv
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://autospare:autospare@localhost:5432/autospare")

# ─── Brand definitions ────────────────────────────────────────────────────────
# (name, name_he, group_name, country, region, is_luxury, is_electric_focused, website)

BRANDS = [
    # ── Honda Group ──────────────────────────────────────────────────────────
    ("Honda",       "הונדה",     "Honda Group",   "Japan",   "Asia",    False, False, "https://www.honda.com"),
    ("Acura",       "אקורה",     "Honda Group",   "Japan",   "Asia",    True,  False, "https://www.acura.com"),

    # ── Stellantis ───────────────────────────────────────────────────────────
    ("Stellantis",  "סטלאנטיס",  "Stellantis",    "Netherlands", "Europe", False, False, "https://www.stellantis.com"),
    ("Citroën",     "סיטרואן",   "Stellantis",    "France",  "Europe",  False, False, "https://www.citroen.com"),
    ("Dodge",       "דודג'",     "Stellantis",    "USA",     "America", False, False, "https://www.dodge.com"),
    ("RAM",         "ראם",       "Stellantis",    "USA",     "America", False, False, "https://www.ramtrucks.com"),
    ("DS Automobiles", "DS",     "Stellantis",    "France",  "Europe",  True,  False, "https://www.dsautomobiles.com"),
    ("Vauxhall",    "ווקסהול",   "Stellantis",    "UK",      "Europe",  False, False, "https://www.vauxhall.co.uk"),
    ("Maserati",    "מזראטי",    "Stellantis",    "Italy",   "Europe",  True,  False, "https://www.maserati.com"),
    ("Jeep",        "ג'יפ",      "Stellantis",    "USA",     "America", False, False, "https://www.jeep.com"),
    ("Alfa Romeo",  "אלפא רומאו", "Stellantis",   "Italy",   "Europe",  True,  False, "https://www.alfaromeo.com"),
    ("FIAT",        "פיאט",      "Stellantis",    "Italy",   "Europe",  False, False, "https://www.fiat.com"),
    ("Peugeot",     "פיג'ו",     "Stellantis",    "France",  "Europe",  False, False, "https://www.peugeot.com"),
    ("Opel",        "אופל",      "Stellantis",    "Germany", "Europe",  False, False, "https://www.opel.com"),
    ("Chrysler",    "קרייסלר",   "Stellantis",    "USA",     "America", False, False, "https://www.chrysler.com"),
    ("Lancia",      "לנצ'יה",    "Stellantis",    "Italy",   "Europe",  False, False, "https://www.lancia.com"),

    # ── Hyundai Motor Group ──────────────────────────────────────────────────
    ("Hyundai",     "יונדאי",    "Hyundai Motor Group", "South Korea", "Asia", False, False, "https://www.hyundai.com"),
    ("Kia",         "קיה",       "Hyundai Motor Group", "South Korea", "Asia", False, False, "https://www.kia.com"),
    ("Genesis",     "ג'נסיס",    "Hyundai Motor Group", "South Korea", "Asia", True,  False, "https://www.genesis.com"),

    # ── Mercedes-Benz Group ──────────────────────────────────────────────────
    ("Mercedes-Benz", "מרצדס",   "Mercedes-Benz Group", "Germany", "Europe", True,  False, "https://www.mercedes-benz.com"),
    ("Smart",       "סמארט",     "Mercedes-Benz Group", "Germany", "Europe", False, True,  "https://www.smart.com"),
    ("Maybach",     "מייבאך",    "Mercedes-Benz Group", "Germany", "Europe", True,  False, "https://www.mercedes-benz.com/maybach"),

    # ── General Motors ───────────────────────────────────────────────────────
    ("General Motors", "ג'נרל מוטורס", "General Motors", "USA", "America", False, False, "https://www.gm.com"),
    ("Chevrolet",   "שברולט",    "General Motors", "USA",    "America", False, False, "https://www.chevrolet.com"),
    ("Cadillac",    "קדילאק",    "General Motors", "USA",    "America", True,  False, "https://www.cadillac.com"),
    ("GMC",         "GMC",       "General Motors", "USA",    "America", False, False, "https://www.gmc.com"),
    ("Buick",       "ביואיק",    "General Motors", "USA",    "America", False, False, "https://www.buick.com"),
    ("Holden",      "הולדן",     "General Motors", "Australia", "Asia", False, False, "https://www.holden.com.au"),

    # ── Nissan Group (Renault-Nissan-Mitsubishi Alliance) ────────────────────
    ("Nissan",      "ניסאן",     "Renault-Nissan-Mitsubishi Alliance", "Japan", "Asia", False, False, "https://www.nissan-global.com"),
    ("Infiniti",    "אינפיניטי", "Renault-Nissan-Mitsubishi Alliance", "Japan", "Asia", True,  False, "https://www.infiniti.com"),
    ("Datsun",      "דאטסון",    "Renault-Nissan-Mitsubishi Alliance", "Japan", "Asia", False, False, "https://www.datsun.com"),
    ("Mitsubishi",  "מיצובישי",  "Renault-Nissan-Mitsubishi Alliance", "Japan", "Asia", False, False, "https://www.mitsubishi-motors.com"),
    ("Renault",     "רנו",       "Renault-Nissan-Mitsubishi Alliance", "France", "Europe", False, False, "https://www.renault.com"),
    ("Dacia",       "דאצ'יה",    "Renault-Nissan-Mitsubishi Alliance", "Romania", "Europe", False, False, "https://www.dacia.com"),
    ("Renault Samsung", "רנו סמסונג", "Renault-Nissan-Mitsubishi Alliance", "South Korea", "Asia", False, False, "https://www.renaultsamsungm.com"),

    # ── Geely Group ──────────────────────────────────────────────────────────
    ("Geely",       "ג'ילי",     "Geely Group",   "China",   "Asia",    False, False, "https://www.geely.com"),
    ("Volvo",       "וולוו",     "Geely Group",   "Sweden",  "Europe",  True,  False, "https://www.volvocars.com"),
    ("Polestar",    "פולסטאר",   "Geely Group",   "Sweden",  "Europe",  True,  True,  "https://www.polestar.com"),
    ("Lynk & Co",   "לינק אנד קו", "Geely Group", "China",   "Asia",    False, False, "https://www.lynkco.com"),
    ("LEVC",        "LEVC",      "Geely Group",   "UK",      "Europe",  False, True,  "https://www.levc.com"),
    ("Lotus",       "לוטוס",     "Geely Group",   "UK",      "Europe",  True,  False, "https://www.lotuscars.com"),

    # ── Volkswagen Group ─────────────────────────────────────────────────────
    ("Volkswagen",  "פולקסווגן", "Volkswagen Group", "Germany", "Europe", False, False, "https://www.volkswagen.com"),
    ("Audi",        "אאודי",     "Volkswagen Group", "Germany", "Europe", True,  False, "https://www.audi.com"),
    ("Skoda",       "סקודה",     "Volkswagen Group", "Czech Republic", "Europe", False, False, "https://www.skoda-auto.com"),
    ("SEAT",        "סיאט",      "Volkswagen Group", "Spain",  "Europe",  False, False, "https://www.seat.com"),
    ("Cupra",       "קופרה",     "Volkswagen Group", "Spain",  "Europe",  False, False, "https://www.cupraofficial.com"),
    ("Porsche",     "פורשה",     "Volkswagen Group", "Germany", "Europe", True,  False, "https://www.porsche.com"),
    ("Lamborghini", "למבורגיני", "Volkswagen Group", "Italy",  "Europe",  True,  False, "https://www.lamborghini.com"),
    ("Bentley",     "בנטלי",     "Volkswagen Group", "UK",     "Europe",  True,  False, "https://www.bentleymotors.com"),
    ("Bugatti",     "בוגאטי",    "Volkswagen Group", "France", "Europe",  True,  False, "https://www.bugatti.com"),
    ("MAN",         "MAN",       "Volkswagen Group", "Germany", "Europe", False, False, "https://www.man.eu"),

    # ── BMW Group ────────────────────────────────────────────────────────────
    ("BMW",         "ב.מ.ו",     "BMW Group",     "Germany", "Europe",  True,  False, "https://www.bmw.com"),
    ("MINI",        "מיני",      "BMW Group",     "UK",      "Europe",  False, False, "https://www.mini.com"),
    ("Rolls-Royce", "רולס רויס", "BMW Group",     "UK",      "Europe",  True,  False, "https://www.rolls-roycemotorcars.com"),

    # ── Toyota Group ─────────────────────────────────────────────────────────
    ("Toyota",      "טויוטה",    "Toyota Group",  "Japan",   "Asia",    False, False, "https://www.toyota.com"),
    ("Lexus",       "לקסוס",     "Toyota Group",  "Japan",   "Asia",    True,  False, "https://www.lexus.com"),
    ("Daihatsu",    "דייהטסו",   "Toyota Group",  "Japan",   "Asia",    False, False, "https://www.daihatsu.com"),
    ("Hino",        "הינו",      "Toyota Group",  "Japan",   "Asia",    False, False, "https://www.hino.co.jp"),
    ("Subaru",      "סובארו",    "Toyota Group",  "Japan",   "Asia",    False, False, "https://www.subaru.com"),
    ("Suzuki",      "סוזוקי",    "Suzuki Motor",  "Japan",   "Asia",    False, False, "https://www.suzuki.co.jp"),

    # ── Ford Motor Company ───────────────────────────────────────────────────
    ("Ford",        "פורד",      "Ford Motor",    "USA",     "America", False, False, "https://www.ford.com"),
    ("Lincoln",     "לינקולן",   "Ford Motor",    "USA",     "America", True,  False, "https://www.lincoln.com"),

    # ── Tata Motors Group ────────────────────────────────────────────────────
    ("Tata Motors", "טטה מוטורס", "Tata Group",   "India",   "Asia",    False, False, "https://www.tatamotors.com"),
    ("Jaguar",      "יגואר",     "Tata Group",    "UK",      "Europe",  True,  False, "https://www.jaguar.com"),
    ("Land Rover",  "לנד רובר",  "Tata Group",    "UK",      "Europe",  True,  False, "https://www.landrover.com"),

    # ── Chinese brands (independent) ─────────────────────────────────────────
    ("BYD",         "BYD",       "BYD Group",     "China",   "Asia",    False, True,  "https://www.byd.com"),
    ("NIO",         "NIO",       "NIO",           "China",   "Asia",    True,  True,  "https://www.nio.com"),
    ("Xpeng",       "שיאופנג",   "Xpeng",         "China",   "Asia",    False, True,  "https://www.xpeng.com"),
    ("Li Auto",     "לי אוטו",   "Li Auto",       "China",   "Asia",    False, True,  "https://www.lixiang.com"),
    ("GWM",         "GWM",       "Great Wall Motors", "China","Asia",   False, False, "https://www.gwm.com.cn"),
    ("Haval",       "האוואל",    "Great Wall Motors", "China","Asia",   False, False, "https://www.haval.com"),
    ("ORA",         "אורה",      "Great Wall Motors", "China","Asia",   False, True,  "https://www.oraev.com"),
    ("Wey",         "וויי",      "Great Wall Motors", "China","Asia",   True,  False, "https://www.wey.com"),
    ("JAECOO",      "ג'אקו",     "Chery Group",   "China",   "Asia",    False, False, "https://global.jaecoo.com"),
    ("Chery",       "צ'רי",      "Chery Group",   "China",   "Asia",    False, False, "https://www.chery.cn"),
    ("OMODA",       "אומודה",    "Chery Group",   "China",   "Asia",    False, False, "https://global.omoda.com"),
    ("SAIC",        "SAIC",      "SAIC Group",    "China",   "Asia",    False, False, "https://www.saicmotor.com"),
    ("MG",          "MG",        "SAIC Group",    "UK/China","Asia",    False, False, "https://www.mgmotor.com"),
    ("Roewe",       "רואי",      "SAIC Group",    "China",   "Asia",    False, False, "https://www.roewe.com.cn"),
    ("GAC",         "GAC",       "GAC Group",     "China",   "Asia",    False, False, "https://www.gac.com.cn"),
    ("Trumpchi",    "טראמפצ'י",  "GAC Group",     "China",   "Asia",    False, False, "https://www.trumpchi.com"),
    ("Aion",        "איאון",     "GAC Group",     "China",   "Asia",    False, True,  "https://www.aion.com.cn"),
    ("GEN",         "GEN",       "Genesis",       "South Korea", "Asia", True, False, "https://www.genesis.com"),

    # ── Korean independent ───────────────────────────────────────────────────
    ("SsangYong",   "סאנגיונג",  "KG Mobility",   "South Korea", "Asia", False, False, "https://www.ssangyong.co.kr"),
    ("KG Mobility", "KG מוביליטי","KG Mobility",  "South Korea", "Asia", False, False, "https://www.kgmobility.co.kr"),

    # ── European independent ─────────────────────────────────────────────────
    ("Ferrari",     "פרארי",     "Ferrari",       "Italy",   "Europe",  True,  False, "https://www.ferrari.com"),
    ("McLaren",     "מקלארן",    "McLaren Group", "UK",      "Europe",  True,  False, "https://www.mclaren.com"),
    ("Aston Martin","אסטון מרטין","Aston Martin",  "UK",      "Europe",  True,  False, "https://www.astonmartin.com"),
    ("Pagani",      "פאגאני",    "Pagani",        "Italy",   "Europe",  True,  False, "https://www.pagani.com"),
    ("Koenigsegg",  "קניגסג",    "Koenigsegg",    "Sweden",  "Europe",  True,  False, "https://www.koenigsegg.com"),
    ("Saab",        "סאאב",      "Independent",   "Sweden",  "Europe",  False, False, "https://www.saabgroup.com"),
    ("Alpine",      "אלפיין",    "Renault-Nissan-Mitsubishi Alliance", "France", "Europe", True, False, "https://www.alpinecars.com"),

    # ── American independent ─────────────────────────────────────────────────
    ("Tesla",       "טסלה",      "Tesla",         "USA",     "America", True,  True,  "https://www.tesla.com"),
    ("Rivian",      "ריביאן",    "Rivian",        "USA",     "America", False, True,  "https://www.rivian.com"),
    ("Lucid",       "לוסיד",     "Lucid Motors",  "USA",     "America", True,  True,  "https://www.lucidmotors.com"),
]

# Alternate spellings / short names used in parts_catalog imports
# agent uses these to resolve "Mercedes" → "Mercedes-Benz" etc.
ALIASES: dict = {
    "Mercedes-Benz":  ["Mercedes", "Mercedes Benz", "MB", "מרצדס", "מרצדס בנץ"],
    "Genesis":        ["GEN", "ג'נסיס", "Genesis Motors"],
    "Citroën":        ["Citroen", "ציטרואן"],
    "Volkswagen":     ["VW", "פולקסווגן", "Volkswagon"],
    "Hyundai":        ["יונדאי", "Hyundai Motor"],
    "Mitsubishi":     ["מיצובישי", "Mitsubishi Motors"],
    "Chevrolet":      ["שברולט", "Chevy"],
    "Renault":        ["רנו", "Renault Group"],
    "Porsche":        ["פורשה"],
    "Smart":          ["סמארט", "smart"],
    "Suzuki":         ["סוזוקי"],
    "ORA":            ["אורה", "Ora"],
    "JAECOO":         ["ג'אקו", "Jaecoo"],
    "Toyota":         ["טויוטה"],
    "BMW":            ["ב.מ.ו", "בי.אם.וו"],
    "Audi":           ["אאודי"],
    "Ford":           ["פורד"],
    "Kia":            ["קיה"],
    "Honda":          ["הונדה"],
    "Peugeot":        ["פיג'ו", "Peugeout"],
    "FIAT":           ["Fiat", "פיאט"],
    "Jeep":           ["ג'יפ"],
    "Dodge":          ["דודג'"],
    "Nissan":         ["ניסאן"],
    "Subaru":         ["סובארו"],
    "Lexus":          ["לקסוס"],
    "Land Rover":     ["LandRover", "לנד רובר", "Range Rover"],
    "Jaguar":         ["יגואר", "Jaguar Land Rover"],
    "Volvo":          ["וולוו"],
    "Geely":          ["ג'ילי"],
    "MG":             ["Morris Garages", "MG Motor"],
}

async def seed():
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Create table if missing
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS car_brands (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name VARCHAR(100) UNIQUE NOT NULL,
                name_he VARCHAR(100),
                group_name VARCHAR(100),
                country VARCHAR(100),
                region VARCHAR(50),
                is_luxury BOOLEAN NOT NULL DEFAULT FALSE,
                is_electric_focused BOOLEAN NOT NULL DEFAULT FALSE,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                logo_url VARCHAR(500),
                website VARCHAR(500),
                notes TEXT,
                aliases TEXT[],
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """))
        # Add aliases column if it doesn't exist (idempotent)
        await conn.execute(text("""
            ALTER TABLE car_brands ADD COLUMN IF NOT EXISTS aliases TEXT[] DEFAULT '{}'
        """))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_car_brands_name ON car_brands(name)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_car_brands_group ON car_brands(group_name)"))

    inserted = 0
    updated = 0

    async with async_session() as session:
        for (name, name_he, group_name, country, region, is_luxury, is_electric, website) in BRANDS:
            aliases_list = ALIASES.get(name, [])
            await session.execute(text("""
                INSERT INTO car_brands (id, name, name_he, group_name, country, region,
                    is_luxury, is_electric_focused, website, aliases, is_active, created_at, updated_at)
                VALUES (gen_random_uuid(), :name, :name_he, :group_name, :country, :region,
                    :is_luxury, :is_electric, :website, :aliases, true, NOW(), NOW())
                ON CONFLICT (name) DO UPDATE SET
                    name_he = EXCLUDED.name_he,
                    group_name = EXCLUDED.group_name,
                    country = EXCLUDED.country,
                    region = EXCLUDED.region,
                    is_luxury = EXCLUDED.is_luxury,
                    is_electric_focused = EXCLUDED.is_electric_focused,
                    website = EXCLUDED.website,
                    aliases = EXCLUDED.aliases,
                    updated_at = NOW()
            """), {
                "name": name, "name_he": name_he, "group_name": group_name,
                "country": country, "region": region, "is_luxury": is_luxury,
                "is_electric": is_electric, "website": website,
                "aliases": aliases_list,
            })
            inserted += 1

        await session.commit()

    # Summary
    async with engine.connect() as conn:
        r = await conn.execute(text("SELECT COUNT(*) FROM car_brands"))
        total = r.scalar()
        r2 = await conn.execute(text("SELECT region, COUNT(*) as cnt FROM car_brands GROUP BY region ORDER BY cnt DESC"))
        by_region = r2.fetchall()
        r3 = await conn.execute(text("SELECT group_name, COUNT(*) as cnt FROM car_brands GROUP BY group_name ORDER BY cnt DESC LIMIT 10"))
        by_group = r3.fetchall()

    print(f"\n✅ car_brands table ready — {total} brands total")
    print(f"   Processed {len(BRANDS)} brands from seeder")
    print("\n📊 By region:")
    for region, cnt in by_region:
        print(f"   {region}: {cnt}")
    print("\n🏭 Top groups:")
    for grp, cnt in by_group:
        print(f"   {grp}: {cnt} brands")


if __name__ == "__main__":
    asyncio.run(seed())
