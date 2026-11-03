| file:line (baseline) | path in baseline | class (before) | exists in container | env override | after |
|---|---|---|---|---|---|
| harvesters/car_parts_ie_playwright_harvester.py:38 | `/opt/autosparefinder/backend/car_parts_ie_cookies.json` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| harvesters/car_parts_ie_playwright_harvester.py:39 | `/opt/autosparefinder/backend/car_parts_ie_harvest_state.json` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| harvesters/car_parts_ie_playwright_harvester.py:40 | `/opt/autosparefinder/backend/logs` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| importers/bmw_il_direct_import.py:19 | `/opt/autosparefinder/champion_motors_parts.json` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| importers/import_delek_brands.py:72 | `/opt/autosparefinder` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| importers/jaguar_batch_import.py:18 | `/opt/autosparefinder/jaguar_parts_raw.ndjson` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| importers/jaguar_ndjson_import.py:21 | `/opt/autosparefinder/jaguar_parts_raw.ndjson` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| importers/kia_fitment_importer.py:39 | `/opt/autosparefinder/logs/kia_fitment_importer.log` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| importers/lr_import.py:50 | `/opt/autosparefinder/land_rover_parts.json` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | yes | REMOVED |
| importers/repair_manufacturer_import.py:87 | `/opt/autosparefinder/merged_all_final-1.xlsx` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| importers/repair_manufacturer_import.py:88 | `/opt/autosparefinder/backend/data/parts_database.normalized.xlsx` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| importers/repair_manufacturer_import.py:89 | `/opt/autosparefinder/backend/data/full` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| importers/repair_manufacturer_import.py:90 | `/opt/autosparefinder/backend/data/parts_database.xlsx` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| importers/sng_barratt_jaguar_import.py:55 | `/opt/autosparefinder/jaguar_parts_raw.ndjson` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| importers/sng_barratt_jaguar_import.py:56 | `/opt/autosparefinder/logs` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| maintenance/backfill_vw_fitment.py:12 | `/opt/autosparefinder/champion_motors_parts.json` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| scrapers/bydil_scraper.py:71 | `/opt/autosparefinder/bydil_parts.json` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| scrapers/champion_motors_playwright_scraper.py:8 | `/opt/autosparefinder/champion_motors_parts.json` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| scrapers/cm_stealth_scraper.py:9 | `/opt/autosparefinder/champion_motors_parts.json` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| scrapers/lr_lrparts_scraper.py:14 | `/opt/autosparefinder/land_rover_parts.json` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| scrapers/mini_scraper.py:12 | `/opt/autosparefinder/mini_parts.json` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| scrapers/sng_barratt_jaguar_scraper.py:26 | `/opt/autosparefinder/jaguar_parts_raw.ndjson` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| scrapers/sng_barratt_jaguar_scraper.py:27 | `/opt/autosparefinder/jaguar_scraper_progress.json` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| scrapers/sng_barratt_jaguar_scraper.py:28 | `/opt/autosparefinder/logs` | BROKEN_IN_CONTAINER | NO - /opt/autosparefinder does not exist in the container | no | REMOVED |
| maintenance/gmail_oauth_setup.py:56 | `/root/.cache/autosparefinder/gmail_oauth_state.json` | HOST_ONLY | n/a | yes | unchanged (HOST_ONLY by design) |
