# AutoSpareFinder — Supplier Integration Guide
*Last updated: 2026-06-25 | Source: Car Parts Sellers & Manufacturers Study PDF*
*Model: Global dropshipping marketplace — supplier ships directly to Israeli customer*

---

## Active Suppliers (17 wired in supplier_aggregator.py)

### Tier 1 — Real-time API (price shown instantly)
| Supplier | File | Ships IL | Price | Type | Status |
|---------|------|----------|-------|------|--------|
| **eBay** | `ebay_supplier.py` | ✅ varies | Varies | Both | ✅ Live API |
| **AliExpress DS** | `aliexpress_supplier.py` | ✅ | Good | Aftermarket | ✅ Live DS API |
| **Autodoc** | `autodoc_supplier.py` | ✅ (request) | Good | Both | ✅ API (autodoc.eu) |

### Tier 2 — Batch Import + Affiliate Fallback
| Supplier | File | Ships IL | Price | Type | Notes |
|---------|------|----------|-------|------|-------|
| **RockAuto** | `catalog_suppliers.py` | ✅ | Good | Both | Harvester exists; affiliate fallback |
| **Spareto** | `catalog_suppliers.py` | ✅ | Good | Both | Harvester exists; affiliate fallback |

### Tier 3 — Ships Israel, Affiliate Link (click-through)
| Supplier | File | Ships IL | Price | Type | Specialty |
|---------|------|----------|-------|------|-----------|
| **PartSouq** | `partsouq_supplier.py` | ✅ | Good | OEM | Middle East OEM focus |
| **Amayama** | `amayama_supplier.py` | ✅ | Good | OEM | Japanese/Korean OEM |
| **Alvadi** | `catalog_suppliers.py` | ✅ | Good | Both | EU aftermarket + OEM |
| **Cars245** | `catalog_suppliers.py` | ✅ | Good | Both | Wide EU catalog |
| **FCP Euro** | `catalog_suppliers.py` | ✅ | Premium | Both | European brands |
| **Summit Racing** | `catalog_suppliers.py` | ✅ | Average | Both | US performance |
| **Fitinpart** | `catalog_suppliers.py` | ✅ | Average | Both | Asian market |
| **Pelican Parts** | `catalog_suppliers.py` | ✅ | Premium | Both | European specialty |
| **ECS Tuning** | `catalog_suppliers.py` | ✅ | Premium | Both | European tuning |
| **Toyota Parts Deal** | `catalog_suppliers.py` | ✅ | Average | OEM | Toyota/Scion only |
| **Ford Parts Giant** | `catalog_suppliers.py` | ✅ | Average | OEM | Ford only |
| **Hyundai Parts Deal** | `catalog_suppliers.py` | ✅ | Average | OEM | Hyundai only |

---

## Skipped (reasons)
| Supplier | Reason |
|---------|--------|
| Mercedes-Benz Used Parts | Used parts only — not relevant |
| BMW Parts Factory | No Israel shipping |
| Bosch/Denso/NGK/Brembo/Mann/Continental/Valeo Direct | No direct consumer shipping — manufacturer wholesale only |

---

## Environment Flags (docker-compose.yml)
All Tier 3 suppliers controlled by `EXTERNAL_ENABLE_*` flags:
```
EXTERNAL_ENABLE_AUTODOC=1
EXTERNAL_ENABLE_ROCKAUTO=1  (already set)
EXTERNAL_ENABLE_SPARETO=1
EXTERNAL_ENABLE_PARTSOUQ=1
EXTERNAL_ENABLE_AMAYAMA=1
EXTERNAL_ENABLE_ALVADI=1
EXTERNAL_ENABLE_CARS245=1
EXTERNAL_ENABLE_FCPEURO=1
EXTERNAL_ENABLE_SUMMIT_RACING=1
EXTERNAL_ENABLE_FITINPART=1
EXTERNAL_ENABLE_PELICAN=1
EXTERNAL_ENABLE_ECS_TUNING=1
EXTERNAL_ENABLE_TOYOTA_PARTS=1
EXTERNAL_ENABLE_FORD_PARTS=1
EXTERNAL_ENABLE_HYUNDAI_PARTS=1
```

---

## Tier 0 — Native Dropship Distributors (Best Fit — from supplier research)
*These are built FOR dropshipping with proper APIs. Superior to the above.*

| Supplier | Location | API | Ships Israel | Status | Specialty |
|---------|----------|-----|-------------|--------|-----------|
| **Turn 14 Distribution** | USA | ✅ REST API | Needs approval | 🔴 NIR todo | 700+ brands (Borla, MagnaFlow, ARB, Fox, K&N…) |
| **Keystone Automotive** | USA | ✅ B2B API | Needs approval | 🔴 NIR todo | 40K+ SKUs, OEM + aftermarket (LKQ subsidiary) |
| **Meyer Distributing** | USA | ✅ API | Needs approval | 🔴 NIR todo | Truck/SUV accessories + tires |
| **American Tire Dist.** | USA | ✅ API | Needs approval | 🔴 NIR todo | Largest US tire distributor |
| **ASAP Network** | USA | ✅ API | Depends | 🟠 Research | Supplier network aggregator |
| **TireConnect** | USA | ✅ API | Needs approval | 🟡 Research | Tires specifically |
| **ifndautoparts** | UK | ✅ API | Likely ✅ | 🟠 REX todo | UK marketplace — easier EU/Israel |
| **Smart Part** | Serbia | ✅ API | Likely ✅ | 🟠 REX todo | EU parts — straightforward Israel shipping |

**Why these are better than the previous list:**
- Built for dropshipping from day 1 (not retrofitted)
- API includes `ship_to_address` = customer's home
- No customer ever sees the supplier
- One API call = order placed + tracking returned

---

## Dropshipping Flow
```
Customer searches → results from all 17+ suppliers
  ├── Priced (API): eBay, AliExpress, Autodoc → show price immediately
  ├── Batch-priced: RockAuto/Spareto → price from our harvested DB
  └── Affiliate: Others → "Check price at supplier" button

Customer selects → we charge via Stripe
  └── Stripe Issuing card (SUPPLIER_ISSUING_CARD_ID) pays the supplier
       → Supplier ships directly to Israeli customer address
```

---

## Revenue Model
| Supplier Tier | Margin |
|---|---|
| AliExpress DS | 30-50% markup on USD |
| eBay | Affiliate commission |
| Autodoc | 15-25% markup on EUR |
| Tier 3 affiliates | Affiliate commission (redirect) |
| IL importers (samelet etc.) | 45% markup (existing policy) |
