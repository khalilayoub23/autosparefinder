# Clean Re-Import List (Parts + OEM)

Generated: 2026-05-18

This file stores the current inactive-parts gap list so we can run a clean re-import by manufacturer as files are uploaded.

## Inactive Gap Summary by Manufacturer

Source query:
- parts_catalog where is_active = FALSE
- grouped by manufacturer
- ordered by total_inactive desc
- limit 20 (returned 19 rows)

| manufacturer | total_inactive | missing_oem | missing_price | missing_category | missing_fitment |
|---|---:|---:|---:|---:|---:|
| Renault | 229527 | 159887 | 161385 | 229104 | 229527 |
| Nissan | 80792 | 0 | 739 | 80792 | 80792 |
| Mercedes-Benz | 79792 | 40352 | 41016 | 79340 | 79792 |
| Chevrolet | 57786 | 29027 | 29075 | 57258 | 9 |
| Hyundai | 42231 | 20745 | 20851 | 42052 | 42231 |
| Suzuki | 25172 | 1927 | 2105 | 25164 | 25170 |
| Mitsubishi | 14821 | 7426 | 7494 | 14740 | 14821 |
| Citroen | 6043 | 1 | 6 | 6043 | 752 |
| Genesis | 5892 | 2244 | 2287 | 5868 | 5892 |
| Chery | 4088 | 0 | 15 | 4088 | 4088 |
| Peugeot | 4048 | 0 | 10 | 4048 | 2794 |
| Xpeng | 3741 | 0 | 24 | 3741 | 3741 |
| Porsche | 2981 | 1475 | 1496 | 2959 | 2981 |
| Smart | 2442 | 1219 | 1234 | 2440 | 2442 |
| Jaecoo | 2023 | 895 | 905 | 2012 | 2023 |
| ORA | 1904 | 774 | 787 | 1899 | 1904 |
| Honda | 1752 | 0 | 9 | 1752 | 1752 |
| Polaris | 1496 | 0 | 8 | 1495 | 1496 |
| JAC | 792 | 0 | 2 | 792 | 792 |

## Import Files Found on Server

- /opt/autosparefinder/backend/data/parts_database.xlsx
- /opt/autosparefinder/backend/data/parts_database.normalized.xlsx
- /opt/autosparefinder/backend/data/full car database.xlsx
- /opt/autosparefinder/merged_all_final-1.xlsx

## Existing Import/Fitment Entry Points

- /opt/autosparefinder/backend/import_parts_db.py:356 -> async def import_parts(selected_sheets: list[str] | None = None)
- /opt/autosparefinder/backend/db_update_agent.py:1900 -> async def backfill_catalog_fitment_from_xls(db: AsyncSession)
- /opt/autosparefinder/backend/db_update_agent.py:2150 -> async def merge_catalog_fitment_from_part_vehicle_fitment(db: AsyncSession)
- /opt/autosparefinder/backend/run_fitment_enrichment_pass.py -> run_fitment_enrichment_pass_async()
- /opt/autosparefinder/backend/build_fitment_action_queues.py -> build_fitment_action_queues()

## Upload + Clean Import Plan

1. Upload source files per manufacturer (OEM + parts).
2. Parse and validate file schema/sheets.
3. Run staged import per manufacturer.
4. Validate inactive gap reduction (OEM, price, category, fitment).
5. Activate validated rows and re-run fitment enrichment pass.
