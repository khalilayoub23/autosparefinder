| file:line | path | exists in container | env override |
|---|---|---|---|
| BACKEND_API_ROUTES.py:1344 | `/app/state/logs/cpie_full_seed.log` | volume (runtime data; not in the tree) | no |
| BACKEND_API_ROUTES.py:1345 | `/app/state/logs` | volume (runtime data; not in the tree) | no |
| BACKEND_API_ROUTES.py:1432 | `/app/maintenance/merge_master_parts.py` | yes | no |
| BACKEND_API_ROUTES.py:1447 | `/app/maintenance/pipeline_parity_check.py` | yes | no |
| BACKEND_API_ROUTES.py:2050 | `/app/state/amayama_ext` | volume (runtime data; not in the tree) | no |
| BACKEND_API_ROUTES.py:2051 | `/app/state/amayama_chrome_profile` | volume (runtime data; not in the tree) | no |
| BACKEND_API_ROUTES.py:2052 | `/root/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome` | n/a | no |
| BACKEND_API_ROUTES.py:2071 | `/tmp/.X` | scratch (created at runtime) | no |
| BACKEND_API_ROUTES.py:2081 | `/app/state/logs/xvfb.log` | volume (runtime data; not in the tree) | no |
| BACKEND_API_ROUTES.py:2116 | `/app/state/logs/amayama_chrome.log` | volume (runtime data; not in the tree) | no |
| BACKEND_API_ROUTES.py:7219 | `/app/uploads` | volume (runtime data; not in the tree) | no |
| BACKEND_API_ROUTES.py:7290 | `/app/importers/supplier_pdf_import.py` | yes | no |
| catalog_scraper.py:4544 | `/app/run_rex_transport_office_pipeline.py` | yes | no |
| catalog_scraper.py:5033 | `/tmp/` | scratch (created at runtime) | no |
| category_embed.py:49 | `/app/state/models/minilm-multilingual` | volume (runtime data; not in the tree) | yes |
| db_update_agent.py:185 | `/app/data/rex_transport_manufacturer_frequency.json` | yes | no |
| email_agent/control.py:46 | `/app/state/email_agent_control.json` | volume (runtime data; not in the tree) | no |
| fb_login_extended.py:7 | `/app/state/fb_browser_session` | volume (runtime data; not in the tree) | no |
| harvesters/acura_browser_harvest.py:108 | `/app/state/acura_parts.json` | volume (runtime data; not in the tree) | no |
| harvesters/champion_motors_harvester.py:42 | `/app/state/logs` | volume (runtime data; not in the tree) | no |
| harvesters/champion_motors_harvester.py:43 | `/app/state` | volume (runtime data; not in the tree) | no |
| harvesters/champion_motors_harvester.py:50 | `/app/state/logs/champion_motors_harvester.log` | volume (runtime data; not in the tree) | no |
| harvesters/champion_motors_harvester.py:59 | `/app/state/champion_motors_parts.json` | volume (runtime data; not in the tree) | no |
| harvesters/champion_motors_harvester.py:287 | `/app/importers/import_champion_motors.py` | yes | no |
| harvesters/kia_israel_harvester.py:44 | `/app/state/logs` | volume (runtime data; not in the tree) | no |
| harvesters/kia_israel_harvester.py:45 | `/app/state` | volume (runtime data; not in the tree) | no |
| harvesters/kia_israel_harvester.py:52 | `/app/state/logs/kia_israel_harvester.log` | volume (runtime data; not in the tree) | no |
| harvesters/kia_israel_harvester.py:59 | `/app/state/kia_israel_parts.json` | volume (runtime data; not in the tree) | no |
| harvesters/kia_israel_harvester.py:221 | `/app/importers/import_kia_israel.py` | yes | no |
| harvesters/toyota_il_harvester.py:37 | `/app/state/logs/toyota_il_harvester.log` | volume (runtime data; not in the tree) | no |
| harvesters/toyota_il_harvester.py:43 | `/app/state/toyota_il_parts.json` | volume (runtime data; not in the tree) | no |
| harvesters/toyota_il_harvester.py:44 | `/app/importers/toyota_il_importer.py` | yes | no |
| harvesters/toyota_il_harvester.py:121 | `/app/state/logs` | volume (runtime data; not in the tree) | no |
| hf_client.py:952 | `/root/.cache/huggingface` | n/a | yes |
| importers/amayama_price_import.py:45 | `/app/state/amayama_parts.json` | volume (runtime data; not in the tree) | yes |
| importers/bmw_il_import.py:32 | `/app/bmw_parts.json` | no (absent from the app tree) | no |
| importers/cadillac_israel_import.py:28 | `/tmp/cadillac_parts.xlsx` | scratch (created at runtime) | no |
| importers/car_parts_ie_import_generic.py:512 | `/tmp/car_parts_ie_import.lock` | scratch (created at runtime) | no |
| importers/catalogs_parts_importer.py:29 | `/tmp/kia_catalog_fitment.tsv` | scratch (created at runtime) | no |
| importers/colmobil_import_v2.py:43 | `/app/state` | volume (runtime data; not in the tree) | no |
| importers/colmobil_import_v2.py:44 | `/app/state/logs/colmobil_import_v2.log` | volume (runtime data; not in the tree) | no |
| importers/colmobil_import_v2.py:45 | `/app/state/logs` | volume (runtime data; not in the tree) | no |
| importers/delek_json_price_update.py:16 | `/app/jlr_parts.json` | no (absent from the app tree) | no |
| importers/delek_json_price_update.py:21 | `/app/ford_parts.json` | no (absent from the app tree) | no |
| importers/delek_json_price_update.py:26 | `/app/mini_parts.json` | no (absent from the app tree) | no |
| importers/delek_multi_importer.py:79 | `/app/state/logs` | volume (runtime data; not in the tree) | no |
| importers/delek_multi_importer.py:86 | `/app/state/logs/delek_multi_importer.log` | volume (runtime data; not in the tree) | no |
| importers/ev_vag_lexus_import.py:43 | `/app/nio_parts.json` | no (absent from the app tree) | no |
| importers/ev_vag_lexus_import.py:43 | `/app/voyah_parts.json` | no (absent from the app tree) | no |
| importers/ev_vag_lexus_import.py:43 | `/app/mhero_parts.json` | no (absent from the app tree) | no |
| importers/ev_vag_lexus_import.py:43 | `/app/mhero2_parts.json` | no (absent from the app tree) | no |
| importers/ev_vag_lexus_import.py:44 | `/app/champion_motors_parts.json` | no (absent from the app tree) | no |
| importers/ev_vag_lexus_import.py:44 | `/app/toyota_il_parts.json` | yes | no |
| importers/fitz_mb_suzuki_import.py:125 | `/app/uploads/` | volume (runtime data; not in the tree) | no |
| importers/fitz_mb_suzuki_import.py:132 | `/app/uploads/` | volume (runtime data; not in the tree) | no |
| importers/fitz_mb_suzuki_import.py:144 | `/app/uploads/` | volume (runtime data; not in the tree) | no |
| importers/fitz_pdf_import.py:21 | `/app/uploads/MITSUBISHI_20260518_161229.pdf` | volume (runtime data; not in the tree) | no |
| importers/fitz_pdf_import.py:22 | `/app/uploads/ORA_20260518_145353.pdf` | volume (runtime data; not in the tree) | no |
| importers/fitz_pdf_import.py:23 | `/app/uploads/SMART_20260518_153424.pdf` | volume (runtime data; not in the tree) | no |
| importers/fitz_pdf_import.py:24 | `/app/uploads/GENESIS_20260518_163814.pdf` | volume (runtime data; not in the tree) | no |
| importers/fitz_pdf_import.py:25 | `/app/uploads/PEUGEOT_20260518_231928.pdf` | volume (runtime data; not in the tree) | no |
| importers/freesbe_importer.py:89 | `/app/state/freesbe_import_progress.json` | volume (runtime data; not in the tree) | no |
| importers/freesbe_recovery.py:67 | `/app/importers` | yes | no |
| importers/gm_run_import.py:44 | `/tmp/gm_vehicle_parts.json` | scratch (created at runtime) | no |
| importers/gmc_buick_umi_import.py:27 | `/tmp/umi_full_text.txt` | scratch (created at runtime) | no |
| importers/hyundai_genesis_mitsubishi_import.py:142 | `/app/uploads/HYUNDAI_20260518_185642.pdf` | volume (runtime data; not in the tree) | no |
| importers/hyundai_genesis_mitsubishi_import.py:143 | `/app/uploads/HYUNDAI_20260518_191141.pdf` | volume (runtime data; not in the tree) | no |
| importers/hyundai_genesis_mitsubishi_import.py:144 | `/app/uploads/HYUNDAI_20260518_192208.pdf` | volume (runtime data; not in the tree) | no |
| importers/hyundai_genesis_mitsubishi_import.py:145 | `/app/uploads/HYUNDAI_20260518_192233.pdf` | volume (runtime data; not in the tree) | no |
| importers/hyundai_genesis_mitsubishi_import.py:146 | `/app/uploads/HYUNDAI_20260518_194204.pdf` | volume (runtime data; not in the tree) | no |
| importers/hyundai_genesis_mitsubishi_import.py:147 | `/app/uploads/GENESIS_20260518_163814.pdf` | volume (runtime data; not in the tree) | no |
| importers/hyundai_genesis_mitsubishi_import.py:148 | `/app/uploads/MITSUBISHI_20260518_161229.pdf` | volume (runtime data; not in the tree) | no |
| importers/hyundai_genesis_mitsubishi_import.py:149 | `/app/uploads/MITSUBISHI_20260518_161251.pdf` | volume (runtime data; not in the tree) | no |
| importers/hyundai_genesis_mitsubishi_import.py:150 | `/app/uploads/MITSUBISHI_20260518_162714.pdf` | volume (runtime data; not in the tree) | no |
| importers/il_pdf_batch_import.py:35 | `/app/uploads/HYUNDAI_20260518_185642.pdf` | volume (runtime data; not in the tree) | no |
| importers/il_pdf_batch_import.py:36 | `/app/uploads/MERCEDES-BENZ_20260518_154959.pdf` | volume (runtime data; not in the tree) | no |
| importers/il_pdf_batch_import.py:37 | `/app/uploads/MITSUBISHI_20260518_161229.pdf` | volume (runtime data; not in the tree) | no |
| importers/il_pdf_batch_import.py:38 | `/app/uploads/SUZUKI_20260518_170146.pdf` | volume (runtime data; not in the tree) | no |
| importers/il_pdf_batch_import.py:39 | `/app/uploads/PEUGEOT_20260518_231928.pdf` | volume (runtime data; not in the tree) | no |
| importers/il_pdf_batch_import.py:40 | `/app/uploads/RENAULT_20260518_223327.pdf` | volume (runtime data; not in the tree) | no |
| importers/il_pdf_batch_import.py:41 | `/app/uploads/SMART_20260518_153424.pdf` | volume (runtime data; not in the tree) | no |
| importers/il_pdf_batch_import.py:42 | `/app/uploads/ORA_20260518_145353.pdf` | volume (runtime data; not in the tree) | no |
| importers/il_pdf_batch_import.py:43 | `/app/uploads/GENESIS_20260518_163814.pdf` | volume (runtime data; not in the tree) | no |
| importers/import_champion_motors.py:57 | `/app/state/champion_motors_parts.json` | volume (runtime data; not in the tree) | yes |
| importers/import_kia_israel.py:37 | `/app/state/kia_israel_parts.json` | volume (runtime data; not in the tree) | yes |
| importers/isuzu_excel_import.py:22 | `/tmp/isuzuFile.xlsx` | scratch (created at runtime) | no |
| importers/kia_il_direct_import.py:23 | `/tmp/Kia_oem.json` | scratch (created at runtime) | no |
| importers/kiapartsnow_fitment_importer.py:31 | `/app/logs` | no (absent from the app tree) | no |
| importers/kiapartsnow_fitment_importer.py:32 | `/app/logs/kiapartsnow_fitment.log` | no (absent from the app tree) | no |
| importers/lexus_pdf_import.py:43 | `/app/uploads/LEXUS_20260612_082412.pdf` | volume (runtime data; not in the tree) | yes |
| importers/lr_subaru_byd_import.py:12 | `/app/land_rover_parts.json` | no (absent from the app tree) | no |
| importers/lr_subaru_byd_import.py:12 | `/app/subaru_il_parts.json` | yes | no |
| importers/lr_subaru_byd_import.py:12 | `/app/bydil_parts.json` | no (absent from the app tree) | no |
| importers/mct_importer.py:44 | `/app/state/logs` | volume (runtime data; not in the tree) | no |
| importers/mct_importer.py:51 | `/app/state/logs/mct_importer.log` | volume (runtime data; not in the tree) | no |
| importers/mixed_brands_import.py:218 | `/app/uploads/MIXED` | volume (runtime data; not in the tree) | no |
| importers/porsche_import.py:36 | `/app/porsche_prices.pdf` | no (absent from the app tree) | yes |
| importers/renault_pdf_import.py:129 | `/app/uploads/RENAULT_20260518_223327.pdf` | volume (runtime data; not in the tree) | no |
| importers/rockauto_browser_import.py:67 | `/tmp/rockauto_daewoo.json` | scratch (created at runtime) | no |
| importers/rockauto_price_import.py:40 | `/app/state/rockauto_parts.json` | volume (runtime data; not in the tree) | yes |
| importers/samelet_xls_export.py:10 | `/app/uploads` | volume (runtime data; not in the tree) | no |
| importers/subaru_il_importer.py:59 | `/app/subaru_il_parts.json` | yes | no |
| importers/toyota_il_importer.py:70 | `/app/state/toyota_il_parts.json` | volume (runtime data; not in the tree) | yes |
| importers/toyota_il_importer.py:71 | `/app/state/toyota_il_parts.json` | volume (runtime data; not in the tree) | yes |
| importers/toyota_il_importer.py:72 | `/app/toyota_il_parts.json` | yes | yes |
| importers/xls_prices_import.py:19 | `/app/data/parts_database.xlsx` | yes | no |
| importers/zeekr_full_import.py:77 | `/app/uploads/ZEEKER001_*.pdf` | volume (runtime data; not in the tree) | no |
| importers/zeekr_full_import.py:483 | `/app/meili_sync.py` | yes | no |
| importers/zeekr_pdf_import.py:165 | `/app/uploads/ZEEKER001_20260519_131930.pdf` | volume (runtime data; not in the tree) | no |
| importers/zeekr_pdf_import.py:166 | `/app/uploads/ZEEKER001_20260519_160740.pdf` | volume (runtime data; not in the tree) | no |
| importers/zeekr_pdf_import.py:167 | `/app/uploads/ZEEKER001_20260526_103011.pdf` | volume (runtime data; not in the tree) | no |
| importers/zeekr_pdf_import.py:168 | `/app/uploads/ZEEKER001_20260526_103745.pdf` | volume (runtime data; not in the tree) | no |
| inproc_job_marker.py:25 | `/app/state/inproc_jobs` | volume (runtime data; not in the tree) | yes |
| maintenance/build_part_thumbnails.py:121 | `/app/state/thumb_clearance_failed.json` | volume (runtime data; not in the tree) | yes |
| maintenance/categorize_parts_batch.py:43 | `/app/state/categorize_improve_cursor.json` | volume (runtime data; not in the tree) | yes |
| maintenance/fb_cookie_import.py:44 | `/app/state` | volume (runtime data; not in the tree) | yes |
| maintenance/fetch_embed_model.py:30 | `/app/state/models/minilm-multilingual` | volume (runtime data; not in the tree) | yes |
| maintenance/merge_master_parts.py:389 | `/app/state/merge_master_cursor.json` | volume (runtime data; not in the tree) | no |
| maintenance/run_fitment_step1_parity_check.py:16 | `/app/data/fitment_step1_parity_report.json` | no (absent from the app tree) | no |
| meili_checkpoint_state.py:22 | `/app/state/meili_sync_checkpoint.json` | volume (runtime data; not in the tree) | yes |
| meili_sync.py:49 | `/app/state/meili_sync_checkpoint.json` | volume (runtime data; not in the tree) | no |
| meili_sync.py:421 | `/tmp/meili_sync.lock` | scratch (created at runtime) | no |
| oempartsonline_importer.py:667 | `/tmp/` | scratch (created at runtime) | no |
| routes/files.py:18 | `/app/uploads` | volume (runtime data; not in the tree) | no |
| routes/system.py:113 | `/app/state/asap` | volume (runtime data; not in the tree) | no |
| routes/system.py:121 | `/app/importers/asap_import.py` | yes | no |
| routes/system.py:124 | `/app/state/logs` | volume (runtime data; not in the tree) | no |
| routes/system.py:304 | `/tmp/` | scratch (created at runtime) | no |
| routes/system.py:307 | `/tmp/` | scratch (created at runtime) | no |
| routes/system.py:309 | `/app/importers/rockauto_price_import.py` | yes | no |
| routes/system.py:317 | `/tmp/` | scratch (created at runtime) | no |
| routes/system.py:320 | `/tmp/` | scratch (created at runtime) | no |
| routes/system.py:322 | `/app/importers/amayama_price_import.py` | yes | no |
| routes/system.py:329 | `/tmp/` | scratch (created at runtime) | no |
| routes/system.py:332 | `/tmp/` | scratch (created at runtime) | no |
| routes/system.py:334 | `/app/oempartsonline_importer.py` | yes | no |
| routes/system.py:345 | `/tmp/` | scratch (created at runtime) | no |
| routes/system.py:348 | `/tmp/` | scratch (created at runtime) | no |
| routes/system.py:349 | `/app/importers/car_parts_ie_import_generic.py` | yes | no |
| runtime_identity.py:58 | `/app/state/runtime_identity` | volume (runtime data; not in the tree) | yes |
| scrapers/audi_playwright_scraper.py:18 | `/tmp/audi_parts.json` | scratch (created at runtime) | no |
| scrapers/bdv_playwright_scraper.py:299 | `/tmp/bdv_parts.json` | scratch (created at runtime) | no |
| scrapers/eliteparts_scraper.py:453 | `/app/meili_sync.py` | yes | no |
| scrapers/febest_scraper.py:38 | `/tmp/febest_progress.json` | scratch (created at runtime) | no |
| scrapers/gm_playwright_scraper.py:21 | `/tmp/gm_vehicle_parts.json` | scratch (created at runtime) | no |
| scrapers/lr_playwright_scraper.py:7 | `/tmp/lr_parts.json` | scratch (created at runtime) | no |
| scrapers/oem_parts_online_scraper.py:253 | `/tmp/` | scratch (created at runtime) | no |
| scripts/restart_preflight.py:39 | `/app/importers/` | yes | no |
| scripts/restart_preflight.py:39 | `/app/maintenance/` | yes | no |
| scripts/restart_preflight.py:39 | `/app/scrapers/` | yes | no |
| scripts/restart_workers.py:53 | `/app/state/worker_state.json` | volume (runtime data; not in the tree) | yes |
| scripts/restart_workers.py:54 | `/app/state/logs` | volume (runtime data; not in the tree) | yes |
| social/facebook_browser/fb_browser_login.py:52 | `/app/state` | volume (runtime data; not in the tree) | yes |
| social/facebook_browser/session.py:47 | `/app/state` | volume (runtime data; not in the tree) | yes |
| social/facebook_browser/session.py:49 | `/app/state` | volume (runtime data; not in the tree) | yes |
| social/facebook_browser/session.py:53 | `/app/state` | volume (runtime data; not in the tree) | yes |
| social/noa_ops.py:438 | `/app/state` | volume (runtime data; not in the tree) | yes |
| social/video_gen.py:430 | `/tmp/product_short.mp4` | scratch (created at runtime) | no |
| social/video_gen.py:437 | `/tmp/coming_soon.mp4` | scratch (created at runtime) | no |
| thumbnail_idle_state.py:25 | `/app/state/thumbnail_import_idle.json` | volume (runtime data; not in the tree) | yes |
