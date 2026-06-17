#!/usr/bin/env python3
"""
Comprehensive bulk harvester — targets 10M parts.
Strategy:
  1. Run OEM Parts Online for all 19 supported brands (full model coverage)
  2. Run REX brand discovery with target=50,000 for ALL 40+ brands (AutoDoc, Febest, official sites)
  3. Run AutoDoc vehicle-based search for each brand+category combination
  
Runs continuously, brand by brand, until catalog grows significantly.
"""
import asyncio, sys, os, time
sys.path.insert(0, "/app")
os.chdir("/app")

# ── Config ────────────────────────────────────────────────────────────────
OEM_BRANDS = [
    "toyota","honda","nissan","ford","bmw","hyundai","kia",
    "mazda","subaru","mitsubishi","volvo","jaguar","landrover",
    "porsche","lexus","infiniti","acura","mopar","vw",
]

# All brands REX should discover (AutoDoc + official sites + eBay)
ALL_BRANDS = [
    # Already have many parts but need more model coverage
    "Toyota","BMW","Kia","Jaguar","Lexus","Mazda","Volvo","Nissan",
    "Ford","Renault","Honda","Infiniti","Hyundai","Mitsubishi","Chevrolet",
    "Land Rover","Chrysler","Mercedes-Benz","Audi","Suzuki","Subaru",
    "Volkswagen","Acura","Mercedes","Opel","SsangYong","BYD",
    # Currently 0 or low coverage — high priority
    "Peugeot","Citroen","Skoda","SEAT","Alfa Romeo","Fiat","Dacia",
    "Mini","Jeep","RAM","Cadillac","GMC","Buick",
    "Tesla","Geely","MG","Haval","Chery","Smart","ORA","Saab",
    "Isuzu","Maserati","Rover","Daewoo","VOYAH","Maxus","GWM",
]

async def main():
    print(f"[bulk_harvest] Starting comprehensive harvest targeting 10M parts", flush=True)
    print(f"[bulk_harvest] Brands targeted: {len(ALL_BRANDS)}", flush=True)
    
    # Phase 1: OEM Parts Online — highest quality, full model/year coverage
    print(f"\n[bulk_harvest] === PHASE 1: OEM Parts Online ({len(OEM_BRANDS)} brands) ===", flush=True)
    try:
        from catalog_scraper import run_oempartsonline_all_brands
        result = await run_oempartsonline_all_brands(brands=OEM_BRANDS, max_models=0)
        print(f"[bulk_harvest] OEM Online done: {result}", flush=True)
    except Exception as e:
        print(f"[bulk_harvest] OEM Online error: {e}", flush=True)
    
    # Phase 2: REX brand discovery with 50K target for ALL brands
    # Process in batches of 10 brands to avoid timeouts
    print(f"\n[bulk_harvest] === PHASE 2: REX Discovery target=50000 ({len(ALL_BRANDS)} brands) ===", flush=True)
    try:
        from catalog_scraper import run_brand_discovery
        batch_size = 10
        for i in range(0, len(ALL_BRANDS), batch_size):
            batch = ALL_BRANDS[i:i+batch_size]
            print(f"\n[bulk_harvest] REX batch {i//batch_size+1}: {batch}", flush=True)
            try:
                result = await run_brand_discovery(
                    brands=batch,
                    target=50000,   # 50K parts per brand target
                    per_run=batch_size,
                )
                inserted = result.get("total_inserted", 0)
                print(f"[bulk_harvest] Batch done: {inserted} new parts", flush=True)
            except Exception as e:
                print(f"[bulk_harvest] Batch error: {e}", flush=True)
            await asyncio.sleep(5)
    except Exception as e:
        print(f"[bulk_harvest] REX discovery error: {e}", flush=True)
    
    print(f"\n[bulk_harvest] COMPLETE — all phases done", flush=True)

asyncio.run(main())
