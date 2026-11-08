# Israeli supplier VAT-basis audit — 2026-11-05 (read-only; nothing in production changed)

Method: `backend/maintenance/audit_supplier_vat_basis.py` (read-only). Median of `supplier_parts.price_ils / parts_catalog.importer_price_ils` over up to 400 priced offers per supplier. 1.00 = stored cost is the ex-VAT cost; 1.18 = stored cost contains 18% VAT; 1.45 = stored cost already contains the 45% margin. This measures what is STORED, not what the supplier website says; website wording for each IL supplier is NOT yet re-verified (status below).

## Findings
1. **Motorstore IL — unknown, no website evidence.** motorstore.co.il home, /contract (תקנון), /about and a product page (Castrol 5W30, 220 ₪, JSON-LD price 220.0 ILS) contain no statement that prices include or exclude VAT (checked 2026-11-05). Its 2,325 priced offers are not scraped site prices at all: stored price = 1.4391 x importer cost (x1.4377 / x1.6965 / x1.00 clusters), all stamped updated_at 2026-07-28 18:02:43, SKUs are Renault/Nissan/Mercedes catalogue numbers, 0 have a supplier_url (0 customer-visible). Classification: `unknown`; keep blocked.
2. **Double VAT (stored price already includes 18%)**: 15 suppliers / 142,078 customer-visible offers (Colmobil x6, Delek Ford/MAXUS/Voyah, BMW Delek, Mazda, MCT, Kia Israel, Champion Motors IL, NIO IL). Live proof: Hyundai 251002E272 stored 3766.41 (= importer cost 3191.00 x 1.18) -> customers see **6444.32**; correct by policy ~5461.29 (-15.2%).
3. **Margin already inside the stored cost (not VAT, found while auditing)**: 10 suppliers / 155,775 visible offers (Samelet brands, BYD, Champion Motors). Live proof: Fiat 000000002000034800 stored 262.08 (= base_price) -> customers see **448.42**; importer-cost basis gives 309.27 (= max_price_ils 309.25). Needs an importer-level fix, not a VAT flag.

## Per-supplier table (stored basis)
| Supplier | Offers | Priced | Customer-visible | Median stored/importer-cost | Stored basis (evidence) |
|---|---:|---:|---:|---:|---|
| Abarth Official Importer Israel | 5732 | 5416 | 5416 | 1.45 | unknown |
| Alfa Romeo Official Importer Israel | 5718 | 5451 | 5451 | 1.45 | unknown |
| AutoParts Pro IL | 115116 | 115115 | 0 | 1.17 | inclusive |
| BMW Delek Motors | 44386 | 44386 | 24389 | 1.1884 | inclusive |
| BYD IL (Shlomo Motors) | 6875 | 6875 | 6875 | 1.45 | unknown |
| Champion Motors | 64544 | 64544 | 64544 | 1.45 | unknown |
| Champion Motors IL | 6373 | 6373 | 3364 | 1.18 | inclusive |
| Colmobil - Genesis Israel | 4514 | 4514 | 4514 | 1.1817 | inclusive |
| Colmobil - Hyundai Israel | 21920 | 21920 | 21920 | 1.1819 | inclusive |
| Colmobil - JAECOO Israel | 4798 | 4798 | 4798 | 1.182 | inclusive |
| Colmobil - Mitsubishi Israel | 7025 | 7025 | 7025 | 1.182 | inclusive |
| Colmobil - ORA Israel | 1386 | 1386 | 1386 | 1.1814 | inclusive |
| Colmobil - Smart Israel | 1825 | 1825 | 1825 | 1.1811 | inclusive |
| Delek Motors Ford IL | 28767 | 28767 | 22150 | 1.18 | inclusive |
| Delek Motors IL | 1070 | 1070 | 0 | 1.0 | exclusive |
| Delek Motors MAXUS IL | 1206 | 1206 | 679 | 1.18 | inclusive |
| Delek Motors Voyah IL | 1898 | 1898 | 1536 | 1.1806 | inclusive |
| Elite Parts | 1313 | 1313 | 1309 | 1.0 | exclusive |
| Fiat Official Importer Israel | 39204 | 38938 | 38937 | 1.45 | unknown |
| Ford Delek Motors | 24864 | 24864 | 24864 | 1.0 | exclusive |
| Geo Mobility - Zeekr Israel | 2301 | 2301 | 2301 | 1.0 | exclusive |
| home - autoworks | 0 | 0 | 0 | None | unknown |
| Hongqi Official Importer Israel | 0 | 0 | 0 | None | unknown |
| Inbar Group - Land Rover Israel | 4928 | 4927 | 4927 | 1.0 | exclusive |
| Iveco Official Importer Israel | 0 | 0 | 0 | None | unknown |
| Jaguar Land Rover Delek Motors | 0 | 0 | 0 | None | unknown |
| Jeep Official Importer Israel | 6846 | 6676 | 6676 | 1.45 | unknown |
| JLR Delek Motors | 14895 | 14895 | 14895 | 1.0 | exclusive |
| Kia Israel | 30108 | 30108 | 19861 | 1.1805 | inclusive |
| Kia Official Importer Israel | 45320 | 45320 | 45320 | 1.0 | exclusive |
| Leapmotor Israel | 0 | 0 | 0 | None | unknown |
| Lexus Official Importer Israel | 484 | 483 | 0 | 1.711 | unknown |
| Mazda Israel - Delek Motors | 15423 | 15423 | 15423 | 1.1807 | inclusive |
| MCT (Mayer Group) | 76815 | 76815 | 11902 | 1.1806 | inclusive |
| M-HERO 2 Delek Motors | 1174 | 1174 | 1174 | 1.0 | exclusive |
| M-HERO Delek Motors | 1174 | 1174 | 1174 | 1.0 | exclusive |
| MINI Delek Motors | 26037 | 26037 | 26037 | 1.0 | exclusive |
| Motorstore IL | 4396 | 2325 | 0 | 1.4391 | unknown |
| NIO Delek Motors | 2269 | 2269 | 2269 | 0.9538 | unknown |
| NIO Delek Motors IL | 2277 | 2277 | 1306 | 1.1808 | inclusive |
| PORSCHE Official Importer Israel | 0 | 0 | 0 | None | unknown |
| RAM Official Importer Israel | 6841 | 6671 | 6671 | 1.45 | unknown |
| Sandbox Supplier QA | 1 | 1 | 0 | None | unknown |
| Subaru Israel - Samelet Motors | 591 | 591 | 283 | 1.711 | unknown |
| Subaru Official Importer Israel | 36136 | 16880 | 16834 | 1.45 | unknown |
| Toyota Israel - Union Motors | 19128 | 19128 | 19128 | 1.0 | exclusive |
| VOYAH Delek Motors | 1870 | 1870 | 1870 | 0.9831 | exclusive |
| WEY Official Importer Israel | 13086 | 4371 | 4371 | 1.45 | unknown |

## Status of verification
Source-website VAT wording re-verified this session: Motorstore only (no statement found). The other IL suppliers: stored-basis evidence above + importer docstrings (Kia/MCT/Toyota ex-VAT site statements; Colmobil/Delek consumer-incl-VAT) — website re-check PENDING.
