# REX Config And Database Schema

Generated: 2026-04-06T19:27:33Z

## REX Full Config

```json
{
  "agent": "Rex",
  "module": "catalog_scraper.py",
  "runtime": {
    "scrape_enabled": true,
    "scrape_interval_h": 6.0,
    "scrape_batch_size": 200,
    "scrape_request_delay_s": 1.5,
    "scrape_max_errors": 30,
    "proxy_configured": false,
    "proxy_host": null,
    "discovery_enabled": true,
    "discovery_interval_h": 24.0,
    "discovery_target": 120,
    "discovery_per_run": 20,
    "discovery_use_official_sites": true,
    "discovery_official_only": true,
    "discovery_official_max_requests": 18,
    "discovery_official_max_domains": 18,
    "discovery_official_max_urls": 72,
    "discovery_official_max_price_usd": 10000.0,
    "discovery_official_suffixes": [
      "com",
      "co.il",
      "de",
      "co.uk",
      "fr",
      "it",
      "es",
      "nl",
      "be",
      "ch",
      "at",
      "pl",
      "cz",
      "pt",
      "se",
      "no",
      "fi",
      "dk",
      "com.au",
      "co.jp",
      "co.kr",
      "com.br",
      "mx",
      "ae",
      "sa",
      "tr"
    ],
    "usd_to_ils": 3.72
  },
  "source_order": [
    "official_sites",
    "autodoc",
    "ebay"
  ],
  "source_maps": {
    "autodoc_brand_slugs": {
      "Toyota": "toyota",
      "BMW": "bmw",
      "Mercedes": "mercedes-benz",
      "Volkswagen": "vw",
      "Ford": "ford",
      "Audi": "audi",
      "Honda": "honda",
      "Nissan": "nissan",
      "Hyundai": "hyundai",
      "Kia": "kia",
      "Mazda": "mazda",
      "Subaru": "subaru",
      "Skoda": "skoda",
      "Renault": "renault",
      "Peugeot": "peugeot",
      "Citroen": "citroen",
      "Opel": "opel",
      "Volvo": "volvo",
      "Seat": "seat",
      "Lexus": "lexus",
      "Jeep": "jeep",
      "Dodge": "dodge",
      "Chevrolet": "chevrolet",
      "Mitsubishi": "mitsubishi",
      "Suzuki": "suzuki",
      "Fiat": "fiat",
      "Alfa Romeo": "alfa-romeo",
      "Porsche": "porsche",
      "Dacia": "dacia",
      "Mini": "mini",
      "Land Rover": "land-rover",
      "Jaguar": "jaguar",
      "Infiniti": "infiniti",
      "Buick": "buick",
      "Cadillac": "cadillac",
      "GMC": "gmc",
      "RAM": "ram",
      "Geely": "geely",
      "BYD": "byd",
      "MG": "mg",
      "Haval": "haval",
      "Chery": "chery",
      "Tesla": "tesla",
      "Smart": "smart",
      "ORA": "ora",
      "Jaecoo": "jaecoo",
      "Genesis": "genesis"
    },
    "autodoc_categories": [
      [
        "Filters",
        "filters"
      ],
      [
        "Brakes",
        "brakes"
      ],
      [
        "Suspension",
        "suspension"
      ],
      [
        "Engine",
        "engine-parts"
      ],
      [
        "Electrical",
        "electrical"
      ],
      [
        "Steering",
        "steering"
      ],
      [
        "Cooling",
        "cooling"
      ],
      [
        "Exhaust",
        "exhaust"
      ],
      [
        "Transmission",
        "transmission"
      ],
      [
        "Fuel System",
        "fuel-system"
      ]
    ],
    "oem_num_patterns": {
      "Toyota": [
        "\\b\\d{5}-[A-Z0-9]{5}\\b"
      ],
      "BMW": [
        "\\b\\d{2}\\s?\\d{2}\\s?\\d\\s?\\d{3}\\s?\\d{3}\\b"
      ],
      "Mercedes": [
        "\\bA?\\d{3}\\s?\\d{3}\\s?\\d{2}\\s?\\d{2}\\b"
      ],
      "Volkswagen": [
        "\\b\\d[A-Z]\\d{3}\\s?\\d{3}\\s?[A-Z0-9]+\\b"
      ],
      "Honda": [
        "\\b\\d{5}-[A-Z0-9]{3}-[A-Z0-9]{3}\\b"
      ],
      "Ford": [
        "\\b[A-Z]{1,2}\\d[A-Z]-\\d{4,6}-[A-Z0-9]+\\b"
      ],
      "default": [
        "\\b[A-Z]{2,4}[-_]?\\d{4,12}[A-Z0-9]{0,6}\\b",
        "\\b\\d{4,12}[-][A-Z0-9]{3,10}\\b"
      ]
    },
    "official_site_search_urls": {
      "Toyota": [
        "https://autoparts.toyota.com/search?search_str={q}"
      ],
      "Lexus": [
        "https://parts.lexus.com/search?search_str={q}"
      ],
      "Ford": [
        "https://parts.ford.com/shop/en/us/search?q={q}"
      ],
      "Volkswagen": [
        "https://parts.vw.com/search?searchTerm={q}"
      ],
      "Audi": [
        "https://parts.audiusa.com/search?searchTerm={q}"
      ],
      "Subaru": [
        "https://parts.subaru.com/search?searchTerm={q}"
      ],
      "Mazda": [
        "https://parts.mazdausa.com/search?searchTerm={q}"
      ],
      "Nissan": [
        "https://parts.nissanusa.com/search?searchTerm={q}"
      ],
      "Honda": [
        "https://dreamshop.honda.com/s/search?q={q}"
      ]
    },
    "official_brand_domains": {
      "Toyota": "toyota.com",
      "Lexus": "lexus.com",
      "BMW": "bmw.com",
      "Mercedes": "mercedes-benz.com",
      "Volkswagen": "vw.com",
      "Ford": "ford.com",
      "Audi": "audi.com",
      "Honda": "honda.com",
      "Nissan": "nissanusa.com",
      "Hyundai": "hyundai.com",
      "Kia": "kia.com",
      "Mazda": "mazdausa.com",
      "Subaru": "subaru.com",
      "Skoda": "skoda-auto.com",
      "Renault": "renaultgroup.com",
      "Peugeot": "peugeot.com",
      "Citroen": "citroen.com",
      "Opel": "opel.com",
      "Volvo": "volvocars.com",
      "Seat": "seat.com",
      "Jeep": "jeep.com",
      "Dodge": "dodge.com",
      "Chevrolet": "chevrolet.com",
      "Mitsubishi": "mitsubishicars.com",
      "Suzuki": "suzuki.com",
      "Fiat": "fiat.com",
      "Alfa Romeo": "alfaromeo.com",
      "Porsche": "porsche.com",
      "Dacia": "dacia.com",
      "Mini": "mini.com",
      "Land Rover": "landrover.com",
      "Jaguar": "jaguar.com",
      "Infiniti": "infinitiusa.com",
      "Buick": "buick.com",
      "Cadillac": "cadillac.com",
      "GMC": "gmc.com",
      "RAM": "ramtrucks.com",
      "Geely": "geely.com",
      "BYD": "byd.com",
      "MG": "mg.co.uk",
      "Haval": "haval.com",
      "Chery": "cheryinternational.com",
      "Tesla": "tesla.com",
      "Smart": "smart.com",
      "ORA": "ora.co.uk",
      "Jaecoo": "jaecoo-global.com",
      "Genesis": "genesis.com"
    },
    "official_brand_domain_aliases": {
      "Volkswagen": [
        "volkswagen.com"
      ],
      "Mercedes": [
        "mercedes.com",
        "mercedes-benz.co.uk",
        "mercedes-benz.de"
      ],
      "Nissan": [
        "nissan.com",
        "nissan.co.uk",
        "nissan.de"
      ],
      "Mazda": [
        "mazda.com",
        "mazda.co.uk",
        "mazda.de"
      ],
      "Renault": [
        "renault.com",
        "renault.fr"
      ],
      "Skoda": [
        "skoda.com",
        "skoda-auto.de"
      ],
      "Volvo": [
        "volvo.com",
        "volvocars.de",
        "volvocars.co.uk"
      ],
      "Mitsubishi": [
        "mitsubishi-motors.com",
        "mitsubishi-motors.co.uk"
      ],
      "Infiniti": [
        "infiniti.com",
        "infiniti.co.uk"
      ],
      "RAM": [
        "ram.com",
        "ramtrucks.com"
      ]
    },
    "official_search_path_templates": [
      "/search?q={q}",
      "/search?query={q}",
      "/search?searchTerm={q}",
      "/search?search_str={q}",
      "/search?keyword={q}",
      "/parts/search?q={q}",
      "/parts/search?query={q}",
      "/parts?query={q}",
      "/catalog/search?q={q}",
      "/shop/search?q={q}",
      "/s/search?q={q}"
    ],
    "official_discovery_queries": [
      "{brand} genuine parts",
      "{brand} oem part number",
      "{brand} spare parts"
    ],
    "official_discovery_queries_by_suffix": {
      "co.il": [
        "{brand} oem spare parts israel",
        "{brand} genuine parts il"
      ],
      "de": [
        "{brand} ersatzteile",
        "{brand} oem teilenummer"
      ],
      "fr": [
        "{brand} pieces detachees",
        "{brand} numero de piece oem"
      ],
      "it": [
        "{brand} ricambi originali",
        "{brand} codice oem"
      ],
      "es": [
        "{brand} recambios originales",
        "{brand} referencia oem"
      ],
      "co.uk": [
        "{brand} genuine parts uk",
        "{brand} oem part number uk"
      ],
      "com.au": [
        "{brand} genuine parts australia"
      ],
      "co.jp": [
        "{brand} genuine parts japan"
      ]
    },
    "category_hint_map": [
      [
        "Filters",
        [
          "filter",
          "oil filter",
          "air filter",
          "cabin filter",
          "fuel filter"
        ]
      ],
      [
        "Brakes",
        [
          "brake",
          "pad",
          "rotor",
          "disc",
          "caliper",
          "abs"
        ]
      ],
      [
        "Suspension",
        [
          "shock",
          "strut",
          "spring",
          "arm",
          "bushing",
          "ball joint",
          "sway"
        ]
      ],
      [
        "Steering",
        [
          "tie rod",
          "rack",
          "steering",
          "power steering"
        ]
      ],
      [
        "Engine",
        [
          "gasket",
          "timing",
          "belt",
          "chain",
          "piston",
          "valve",
          "head"
        ]
      ],
      [
        "Electrical",
        [
          "sensor",
          "alternator",
          "starter",
          "coil",
          "relay",
          "switch",
          "ecu"
        ]
      ],
      [
        "Transmission",
        [
          "clutch",
          "transmission",
          "gearbox",
          "flywheel",
          "cv axle"
        ]
      ],
      [
        "Cooling",
        [
          "thermostat",
          "water pump",
          "radiator",
          "coolant",
          "fan"
        ]
      ],
      [
        "Exhaust",
        [
          "exhaust",
          "muffler",
          "catalytic",
          "egr",
          "manifold"
        ]
      ],
      [
        "Body",
        [
          "bumper",
          "fender",
          "mirror",
          "headlight",
          "tail light"
        ]
      ],
      [
        "HVAC",
        [
          "ac",
          "compressor",
          "condenser",
          "evaporator",
          "hvac"
        ]
      ],
      [
        "Fuel System",
        [
          "fuel pump",
          "injector",
          "fuel rail",
          "tank"
        ]
      ]
    ]
  }
}
```

## CATALOG Database Schema

- Tables: 28
- Columns: 338
- Foreign keys: 12

### alembic_version

| Column | Data Type | Nullable |
|---|---|---|
| version_num | character varying | NO |\n
### audit_logs

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| user_id | uuid | YES |\n| action | character varying | NO |\n| entity_type | character varying | YES |\n| entity_id | uuid | YES |\n| old_value | jsonb | YES |\n| new_value | jsonb | YES |\n| ip_address | character varying | YES |\n| user_agent | text | YES |\n| created_at | timestamp without time zone | YES |\n
### brand_aliases

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| brand_id | uuid | NO |\n| alias | character varying | NO |\n| normalized | character varying | NO |\n| source | character varying | YES |\n| created_at | timestamp without time zone | NO |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| brand_aliases_brand_id_fkey | brand_id | car_brands.id |\n
### bug_reports

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| user_id | uuid | YES |\n| user_role | character varying | YES |\n| title | character varying | NO |\n| description | text | NO |\n| severity | character varying | YES |\n| platform | character varying | YES |\n| app_version | character varying | YES |\n| screen_name | character varying | YES |\n| endpoint_url | character varying | YES |\n| http_method | character varying | YES |\n| http_status_code | integer | YES |\n| error_trace | text | YES |\n| last_api_calls | jsonb | YES |\n| device_info | jsonb | YES |\n| tech_analysis | jsonb | YES |\n| status | character varying | YES |\n| admin_notes | text | YES |\n| resolved_at | timestamp without time zone | YES |\n| created_at | timestamp without time zone | YES |\n| updated_at | timestamp without time zone | YES |\n
### cache_entries

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| cache_key | character varying | NO |\n| cache_value | jsonb | YES |\n| expires_at | timestamp without time zone | YES |\n| created_at | timestamp without time zone | YES |\n
### car_brands

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| name | character varying | NO |\n| name_he | character varying | YES |\n| group_name | character varying | YES |\n| country | character varying | YES |\n| region | character varying | YES |\n| is_luxury | boolean | NO |\n| is_electric_focused | boolean | NO |\n| is_active | boolean | NO |\n| logo_url | character varying | YES |\n| website | character varying | YES |\n| notes | text | YES |\n| aliases | ARRAY | YES |\n| created_at | timestamp without time zone | NO |\n| updated_at | timestamp without time zone | YES |\n| warranty_years | integer | YES |\n| warranty_km | integer | YES |\n| warranty_notes | text | YES |\n| il_importer | character varying | YES |\n| il_importer_website | character varying | YES |\n| parts_availability | character varying | YES |\n| avg_service_interval_km | integer | YES |\n| popular_models_il | jsonb | YES |\n
### catalog_versions

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| version_tag | character varying | NO |\n| description | text | YES |\n| parts_added | integer | NO |\n| parts_updated | integer | NO |\n| parts_total | integer | NO |\n| source | character varying | YES |\n| triggered_by | uuid | YES |\n| started_at | timestamp without time zone | NO |\n| completed_at | timestamp without time zone | YES |\n| status | character varying | NO |\n| error_log | text | YES |\n| created_at | timestamp without time zone | NO |\n
### job_registry

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| job_id | character varying | NO |\n| job_name | character varying | NO |\n| worker_host | character varying | YES |\n| status | character varying | NO |\n| started_at | timestamp with time zone | NO |\n| completed_at | timestamp with time zone | YES |\n| ttl_seconds | integer | YES |\n| error_message | text | YES |\n| last_heartbeat_at | timestamp with time zone | NO |\n| created_at | timestamp with time zone | NO |\n
### part_aliases

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| part_id | uuid | NO |\n| alias | character varying | NO |\n| language | character varying | NO |\n| created_at | timestamp without time zone | NO |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| part_aliases_part_id_fkey | part_id | parts_catalog.id |\n
### part_cross_reference

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| part_id | uuid | NO |\n| ref_number | character varying | NO |\n| manufacturer | character varying | NO |\n| ref_type | character varying | NO |\n| is_superseded | boolean | NO |\n| superseded_by | character varying | YES |\n| created_at | timestamp without time zone | NO |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| part_cross_reference_part_id_fkey | part_id | parts_catalog.id |\n
### part_variants

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| master_part_id | uuid | NO |\n| catalog_part_id | uuid | NO |\n| quality_level | character varying | NO |\n| manufacturer | character varying | YES |\n| sku | character varying | YES |\n| created_at | timestamp without time zone | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| part_variants_catalog_part_id_fkey | catalog_part_id | parts_catalog.id |\n| part_variants_master_part_id_fkey | master_part_id | parts_master.id |\n
### part_vehicle_fitment

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| part_id | uuid | NO |\n| manufacturer | character varying | NO |\n| model | character varying | NO |\n| year_from | integer | NO |\n| year_to | integer | YES |\n| engine_type | character varying | YES |\n| transmission | character varying | YES |\n| notes | text | YES |\n| created_at | timestamp without time zone | NO |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| part_vehicle_fitment_part_id_fkey | part_id | parts_catalog.id |\n
### parts_catalog

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| sku | character varying | NO |\n| name | character varying | NO |\n| category | character varying | YES |\n| manufacturer | character varying | YES |\n| part_type | character varying | YES |\n| description | text | YES |\n| specifications | jsonb | YES |\n| compatible_vehicles | jsonb | YES |\n| base_price | numeric | YES |\n| is_active | boolean | YES |\n| created_at | timestamp without time zone | YES |\n| updated_at | timestamp without time zone | YES |\n| name_he | character varying | YES |\n| oem_number | character varying | YES |\n| barcode | character varying | YES |\n| weight_kg | numeric | YES |\n| importer_price_ils | numeric | YES |\n| online_price_ils | numeric | YES |\n| min_price_ils | numeric | YES |\n| max_price_ils | numeric | YES |\n| part_condition | character varying | NO |\n| superseded_by_sku | character varying | YES |\n| customs_tariff_code | character varying | YES |\n| is_safety_critical | boolean | NO |\n| search_vector | tsvector | YES |\n| needs_oem_lookup | boolean | NO |\n| master_enriched | boolean | NO |\n| image_embedding | USER-DEFINED | YES |\n| embedding | USER-DEFINED | YES |\n
### parts_images

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| part_id | uuid | NO |\n| file_id | uuid | YES |\n| url | character varying | YES |\n| is_primary | boolean | YES |\n| sort_order | integer | YES |\n| created_at | timestamp without time zone | YES |\n| embedding_generated | boolean | NO |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| parts_images_part_id_fkey | part_id | parts_catalog.id |\n
### parts_master

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| canonical_name | character varying | NO |\n| canonical_name_he | character varying | YES |\n| category | character varying | NO |\n| part_type | character varying | YES |\n| is_safety_critical | boolean | NO |\n| created_at | timestamp without time zone | YES |\n| updated_at | timestamp without time zone | YES |\n
### price_history

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| supplier_part_id | uuid | NO |\n| old_price_ils | numeric | YES |\n| new_price_ils | numeric | NO |\n| old_price_usd | numeric | YES |\n| new_price_usd | numeric | NO |\n| change_pct | numeric | YES |\n| source | character varying | YES |\n| ils_per_usd_rate | numeric | YES |\n| created_at | timestamp without time zone | NO |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| price_history_supplier_part_id_fkey | supplier_part_id | supplier_parts.id |\n
### purchase_orders

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| po_number | character varying | NO |\n| order_id | uuid | YES |\n| supplier_id | uuid | NO |\n| status | character varying | NO |\n| total_usd | numeric | YES |\n| total_ils | numeric | YES |\n| shipping_type | character varying | NO |\n| tracking_number | character varying | YES |\n| shipped_at | timestamp without time zone | YES |\n| received_at | timestamp without time zone | YES |\n| notes | text | YES |\n| created_at | timestamp without time zone | NO |\n| updated_at | timestamp without time zone | NO |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| purchase_orders_supplier_id_fkey | supplier_id | suppliers.id |\n
### scraper_api_calls

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| source | character varying | NO |\n| query | character varying | YES |\n| part_number | character varying | YES |\n| http_status | integer | YES |\n| success | boolean | NO |\n| results_count | integer | YES |\n| response_ms | integer | YES |\n| error_message | text | YES |\n| created_at | timestamp without time zone | NO |\n
### search_misses

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| query | text | NO |\n| normalized_query | text | NO |\n| category | character varying | YES |\n| vehicle_manufacturer | character varying | YES |\n| miss_count | integer | NO |\n| last_seen_at | timestamp without time zone | NO |\n| first_seen_at | timestamp without time zone | NO |\n| triggered_scrape | boolean | NO |\n| created_at | timestamp without time zone | NO |\n| user_id | uuid | YES |\n| notified | boolean | NO |\n
### social_posts

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| content | text | NO |\n| platforms | ARRAY | NO |\n| status | character varying | NO |\n| scheduled_at | timestamp without time zone | YES |\n| published_at | timestamp without time zone | YES |\n| external_post_ids | jsonb | NO |\n| created_by | uuid | NO |\n| approved_by | uuid | YES |\n| rejection_reason | text | YES |\n| created_at | timestamp without time zone | NO |\n| updated_at | timestamp without time zone | NO |\n
### supplier_parts

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| supplier_id | uuid | NO |\n| part_id | uuid | NO |\n| supplier_sku | character varying | YES |\n| price_usd | numeric | NO |\n| price_ils | numeric | YES |\n| shipping_cost_usd | numeric | YES |\n| shipping_cost_ils | numeric | YES |\n| availability | character varying | YES |\n| warranty_months | integer | YES |\n| estimated_delivery_days | integer | YES |\n| last_checked_at | timestamp without time zone | YES |\n| is_available | boolean | YES |\n| created_at | timestamp without time zone | YES |\n| stock_quantity | integer | YES |\n| min_order_qty | integer | NO |\n| supplier_url | character varying | YES |\n| last_in_stock_at | timestamp without time zone | YES |\n| express_available | boolean | NO |\n| express_price_ils | numeric | YES |\n| express_delivery_days | integer | YES |\n| express_cutoff_time | character varying | YES |\n| express_last_checked | timestamp without time zone | YES |\n| updated_at | timestamp without time zone | YES |\n| part_type | character varying | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| supplier_parts_part_id_fkey | part_id | parts_catalog.id |\n| supplier_parts_supplier_id_fkey | supplier_id | suppliers.id |\n
### suppliers

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| name | character varying | NO |\n| country | character varying | YES |\n| website | character varying | YES |\n| api_endpoint | character varying | YES |\n| api_key | text | YES |\n| credentials | jsonb | YES |\n| shipping_info | jsonb | YES |\n| return_policy | jsonb | YES |\n| reliability_score | numeric | NO |\n| is_active | boolean | YES |\n| priority | integer | YES |\n| created_at | timestamp without time zone | YES |\n| updated_at | timestamp without time zone | YES |\n| supports_express | boolean | NO |\n| express_carrier | character varying | YES |\n| express_base_cost_usd | numeric | YES |\n| avg_delivery_days_actual | numeric | YES |\n| rate_limit_per_minute | integer | NO |\n| is_manufacturer | boolean | NO |\n| manufacturer_name | character varying | YES |\n
### system_logs

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| level | character varying | NO |\n| logger_name | character varying | YES |\n| message | text | NO |\n| user_id | uuid | YES |\n| ip_address | character varying | YES |\n| endpoint | character varying | YES |\n| method | character varying | YES |\n| status_code | integer | YES |\n| request_data | jsonb | YES |\n| response_data | jsonb | YES |\n| exception | text | YES |\n| stack_trace | text | YES |\n| created_at | timestamp without time zone | YES |\n
### system_settings

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| key | character varying | NO |\n| value | text | YES |\n| value_type | character varying | YES |\n| description | text | YES |\n| is_public | boolean | YES |\n| updated_by | uuid | YES |\n| updated_at | timestamp without time zone | YES |\n
### truck_brand_aliases

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| brand_id | uuid | NO |\n| alias | character varying | NO |\n| normalized | character varying | NO |\n| source | character varying | YES |\n| created_at | timestamp with time zone | NO |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| truck_brand_aliases_brand_id_fkey | brand_id | truck_brands.id |\n
### truck_brands

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| name | character varying | NO |\n| name_he | character varying | YES |\n| group_name | character varying | YES |\n| country | character varying | YES |\n| region | character varying | YES |\n| is_active | boolean | NO |\n| logo_url | character varying | YES |\n| website | character varying | YES |\n| notes | text | YES |\n| aliases | ARRAY | YES |\n| il_importer | character varying | YES |\n| il_importer_website | character varying | YES |\n| parts_availability | character varying | YES |\n| avg_service_interval_km | integer | YES |\n| popular_models_il | json | YES |\n| created_at | timestamp with time zone | NO |\n| updated_at | timestamp with time zone | NO |\n
### vehicle_hierarchy_xls

| Column | Data Type | Nullable |
|---|---|---|
| id | bigint | NO |\n| manufacturer | text | NO |\n| model | text | NO |\n| sub_model | text | NO |\n| year_from | integer | NO |\n| year_to | integer | NO |\n| year_hint | integer | NO |\n| source_sheet | text | YES |\n| source_tag | text | NO |\n| updated_at | timestamp with time zone | NO |\n
### vehicles

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| license_plate | character varying | YES |\n| manufacturer | character varying | NO |\n| model | character varying | NO |\n| year | integer | NO |\n| vin | character varying | YES |\n| engine_type | character varying | YES |\n| transmission | character varying | YES |\n| fuel_type | character varying | YES |\n| gov_api_data | jsonb | YES |\n| cached_at | timestamp without time zone | YES |\n| created_at | timestamp without time zone | YES |\n
## PII Database Schema

- Tables: 23
- Columns: 249
- Foreign keys: 24

### alembic_version

| Column | Data Type | Nullable |
|---|---|---|
| version_num | character varying | NO |\n
### approval_queue

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| entity_type | character varying | NO |\n| entity_id | uuid | NO |\n| action | character varying | NO |\n| payload | jsonb | NO |\n| status | character varying | NO |\n| requested_by | uuid | YES |\n| resolved_by | uuid | YES |\n| resolution_note | text | YES |\n| created_at | timestamp without time zone | NO |\n| resolved_at | timestamp without time zone | YES |\n| idempotency_key | character varying | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| approval_queue_requested_by_fkey | requested_by | users.id |\n| approval_queue_resolved_by_fkey | resolved_by | users.id |\n
### cart_items

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| cart_id | uuid | NO |\n| part_id | uuid | NO |\n| supplier_part_id | uuid | NO |\n| quantity | integer | NO |\n| unit_price | numeric | NO |\n| added_at | timestamp without time zone | NO |\n| updated_at | timestamp without time zone | NO |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| cart_items_cart_id_fkey | cart_id | carts.id |\n
### carts

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| user_id | uuid | NO |\n| created_at | timestamp without time zone | NO |\n| updated_at | timestamp without time zone | NO |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| carts_user_id_fkey | user_id | users.id |\n
### conversations

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| user_id | uuid | NO |\n| session_id | character varying | YES |\n| context | jsonb | YES |\n| status | character varying | YES |\n| created_at | timestamp without time zone | YES |\n| updated_at | timestamp without time zone | YES |\n| deleted_at | timestamp without time zone | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| conversations_user_id_fkey | user_id | users.id |\n
### invoices

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| order_id | uuid | NO |\n| user_id | uuid | NO |\n| invoice_number | character varying | NO |\n| total_ils | numeric | NO |\n| vat_ils | numeric | NO |\n| issued_at | timestamp without time zone | YES |\n| due_at | timestamp without time zone | YES |\n| pdf_url | character varying | YES |\n| status | character varying | YES |\n| created_at | timestamp without time zone | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| invoices_order_id_fkey | order_id | orders.id |\n| invoices_user_id_fkey | user_id | users.id |\n
### job_failures

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| job_name | character varying | NO |\n| payload | json | YES |\n| error | text | YES |\n| attempts | integer | NO |\n| next_retry_at | timestamp with time zone | YES |\n| status | character varying | NO |\n| created_at | timestamp with time zone | NO |\n| resolved_at | timestamp with time zone | YES |\n| resolved_by | character varying | YES |\n
### login_attempts

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| user_id | uuid | YES |\n| email | character varying | YES |\n| ip_address | character varying | NO |\n| success | boolean | NO |\n| failure_reason | character varying | YES |\n| created_at | timestamp without time zone | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| login_attempts_user_id_fkey | user_id | users.id |\n
### messages

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| conversation_id | uuid | NO |\n| role | character varying | NO |\n| content | text | NO |\n| metadata | jsonb | YES |\n| created_at | timestamp without time zone | YES |\n| deleted_at | timestamp without time zone | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| messages_conversation_id_fkey | conversation_id | conversations.id |\n
### notifications

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| user_id | uuid | NO |\n| title | character varying | NO |\n| message | text | YES |\n| type | character varying | YES |\n| is_read | boolean | YES |\n| data | jsonb | YES |\n| created_at | timestamp without time zone | YES |\n| read_at | timestamp without time zone | YES |\n| channel | character varying | YES |\n| sent_at | timestamp without time zone | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| notifications_user_id_fkey | user_id | users.id |\n
### order_items

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| order_id | uuid | NO |\n| part_id | uuid | YES |\n| supplier_part_id | uuid | YES |\n| part_sku | character varying | YES |\n| name_he | character varying | YES |\n| name_en | character varying | YES |\n| quantity | integer | NO |\n| unit_price | numeric | NO |\n| total_price | numeric | NO |\n| is_express | boolean | YES |\n| created_at | timestamp without time zone | YES |\n| part_name | character varying | NO |\n| manufacturer | character varying | YES |\n| part_type | character varying | YES |\n| supplier_name | character varying | YES |\n| supplier_order_id | character varying | YES |\n| vat_amount | numeric | NO |\n| warranty_months | integer | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| order_items_order_id_fkey | order_id | orders.id |\n
### orders

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| user_id | uuid | NO |\n| vehicle_id | uuid | YES |\n| status | character varying | NO |\n| subtotal | numeric | NO |\n| vat_amount | numeric | NO |\n| shipping_cost | numeric | YES |\n| total_amount | numeric | NO |\n| currency | character varying | YES |\n| notes | text | YES |\n| shipping_address | jsonb | YES |\n| created_at | timestamp without time zone | NO |\n| updated_at | timestamp without time zone | YES |\n| deleted_at | timestamp without time zone | YES |\n| order_number | character varying | NO |\n| discount_amount | numeric | YES |\n| tracking_number | character varying | YES |\n| tracking_url | character varying | YES |\n| estimated_delivery | timestamp without time zone | YES |\n| coupon_code | character varying | YES |\n| shipping_type | character varying | YES |\n| shipped_at | timestamp without time zone | YES |\n| delivered_at | timestamp without time zone | YES |\n| cancelled_at | timestamp without time zone | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| orders_user_id_fkey | user_id | users.id |\n
### part_reviews

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| user_id | uuid | NO |\n| part_id | uuid | NO |\n| order_id | uuid | YES |\n| rating | integer | NO |\n| title | character varying | YES |\n| body | text | YES |\n| is_verified_purchase | boolean | NO |\n| created_at | timestamp without time zone | NO |\n| updated_at | timestamp without time zone | NO |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| part_reviews_order_id_fkey | order_id | orders.id |\n| part_reviews_user_id_fkey | user_id | users.id |\n
### password_resets

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| user_id | uuid | NO |\n| token | character varying | NO |\n| expires_at | timestamp without time zone | NO |\n| used_at | timestamp without time zone | YES |\n| created_at | timestamp without time zone | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| password_resets_user_id_fkey | user_id | users.id |\n
### payments

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| order_id | uuid | NO |\n| user_id | uuid | NO |\n| amount_ils | numeric | NO |\n| currency | character varying | YES |\n| status | character varying | NO |\n| provider | character varying | YES |\n| provider_transaction_id | character varying | YES |\n| last_four | character varying | YES |\n| card_brand | character varying | YES |\n| error_message | text | YES |\n| created_at | timestamp without time zone | YES |\n| updated_at | timestamp without time zone | YES |\n| payment_intent_id | character varying | YES |\n| amount | numeric | NO |\n| payment_method | character varying | YES |\n| stripe_customer_id | character varying | YES |\n| last_4_digits | character varying | YES |\n| paid_at | timestamp without time zone | YES |\n| refunded_at | timestamp without time zone | YES |\n| refund_amount | numeric | YES |\n| refund_reason | character varying | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| payments_order_id_fkey | order_id | orders.id |\n| payments_user_id_fkey | user_id | users.id |\n
### returns

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| order_id | uuid | NO |\n| user_id | uuid | NO |\n| reason | text | YES |\n| status | character varying | YES |\n| refund_amount_ils | numeric | YES |\n| approved_at | timestamp without time zone | YES |\n| created_at | timestamp without time zone | YES |\n| updated_at | timestamp without time zone | YES |\n| item_shipped_at | timestamp without time zone | YES |\n| supplier_confirmed_at | timestamp without time zone | YES |\n| refund_issued_at | timestamp without time zone | YES |\n| supplier_notes | text | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| returns_order_id_fkey | order_id | orders.id |\n| returns_user_id_fkey | user_id | users.id |\n
### stripe_webhook_logs

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| event_id | character varying | NO |\n| event_type | character varying | NO |\n| processed | boolean | NO |\n| payload | json | YES |\n| result | json | YES |\n| created_at | timestamp with time zone | NO |\n| processed_at | timestamp with time zone | YES |\n
### two_factor_codes

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| user_id | uuid | NO |\n| code | character varying | NO |\n| phone | character varying | YES |\n| attempts | integer | YES |\n| expires_at | timestamp without time zone | NO |\n| verified_at | timestamp without time zone | YES |\n| created_at | timestamp without time zone | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| two_factor_codes_user_id_fkey | user_id | users.id |\n
### user_profiles

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| user_id | uuid | NO |\n| address_line1 | character varying | YES |\n| address_line2 | character varying | YES |\n| city | character varying | YES |\n| postal_code | character varying | YES |\n| default_vehicle_id | uuid | YES |\n| marketing_consent | boolean | YES |\n| newsletter_subscribed | boolean | YES |\n| terms_accepted_at | timestamp without time zone | YES |\n| marketing_preferences | jsonb | YES |\n| preferred_language | character varying | YES |\n| avatar_url | character varying | YES |\n| created_at | timestamp without time zone | YES |\n| updated_at | timestamp without time zone | YES |\n| customer_type | character varying | NO |\n| total_orders | integer | NO |\n| total_spent_ils | numeric | NO |\n| is_vip | boolean | NO |\n| vip_since | timestamp without time zone | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| user_profiles_user_id_fkey | user_id | users.id |\n
### user_sessions

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| user_id | uuid | NO |\n| token | character varying | NO |\n| refresh_token | character varying | YES |\n| device_fingerprint | character varying | YES |\n| device_name | character varying | YES |\n| ip_address | character varying | YES |\n| user_agent | text | YES |\n| is_trusted_device | boolean | YES |\n| trusted_until | timestamp without time zone | YES |\n| expires_at | timestamp without time zone | NO |\n| last_used_at | timestamp without time zone | YES |\n| revoked_at | timestamp without time zone | YES |\n| created_at | timestamp without time zone | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| user_sessions_user_id_fkey | user_id | users.id |\n
### user_vehicles

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| user_id | uuid | NO |\n| vehicle_id | uuid | NO |\n| nickname | character varying | YES |\n| is_primary | boolean | YES |\n| created_at | timestamp without time zone | YES |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| user_vehicles_user_id_fkey | user_id | users.id |\n
### users

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| email | character varying | NO |\n| phone | character varying | YES |\n| password_hash | character varying | YES |\n| full_name | character varying | NO |\n| role | character varying | NO |\n| is_active | boolean | NO |\n| is_verified | boolean | NO |\n| is_admin | boolean | NO |\n| failed_login_count | integer | NO |\n| locked_until | timestamp without time zone | YES |\n| created_at | timestamp without time zone | NO |\n| updated_at | timestamp without time zone | YES |\n| is_super_admin | boolean | NO |\n| oauth_provider | character varying | YES |\n| oauth_id | character varying | YES |\n
### wishlist_items

| Column | Data Type | Nullable |
|---|---|---|
| id | uuid | NO |\n| user_id | uuid | NO |\n| part_id | uuid | NO |\n| added_at | timestamp without time zone | NO |\n
Foreign keys:

| Constraint | Column | References |
|---|---|---|
| wishlist_items_user_id_fkey | user_id | users.id |\n
