# AutoSpareFinder — Partner API (v1)

A small, stable REST API for external sites and developers to search the catalog and get
**customer-ready prices**. It returns only the data a partner needs — no supplier names, no cost,
no margin, no internal fields.

- **Base URL:** `https://autosparefinder.co.il/api/public/v1`
- **Auth:** every endpoint except `/health` requires an API key in the **`X-API-Key`** header.
- **Format:** JSON. Prices are in **ILS**, already customer-facing (our margin applied, VAT per
  Israeli law — 18% when the part is sourced locally, 0% when sourced abroad).
- **Interactive docs:** `https://autosparefinder.co.il/docs` (endpoints tagged **Public API**).

## Getting a key
Keys are issued by the AutoSpareFinder team. Each key has a name, a per-minute rate limit
(default 60/min), and can be revoked at any time. The raw key is shown once — store it safely.

```
X-API-Key: asf_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

## Endpoints

### `GET /health`  (no auth)
Liveness check.
```bash
curl https://autosparefinder.co.il/api/public/v1/health
# {"status":"ok","service":"AutoSpareFinder Partner API","version":"1"}
```

### `GET /search`
Search by free text and/or filters. Provide at least `q` or `manufacturer`.

| Param | Type | Notes |
|---|---|---|
| `q` | string | Free text (part name / OEM). Ranked by relevance. |
| `manufacturer` | string | Car brand, e.g. `Toyota`. |
| `category` | string | Category slug, e.g. `brakes`, `filters`. |
| `limit` | int | 1–50 (default 20). |
| `offset` | int | 0–1000 (default 0). |

```bash
curl -H "X-API-Key: $KEY" \
  "https://autosparefinder.co.il/api/public/v1/search?q=oil%20filter&manufacturer=Toyota&limit=5"
```

### `GET /parts/{part_id}`
One part by its `part_id` (as returned by search).
```bash
curl -H "X-API-Key: $KEY" \
  "https://autosparefinder.co.il/api/public/v1/parts/00437f73-2d26-4ba5-9320-23e9fe136e88"
```

### `GET /fitment`
Parts that fit a specific vehicle.

| Param | Type | Notes |
|---|---|---|
| `make` | string | **required** — e.g. `Toyota` |
| `model` | string | **required** — e.g. `Corolla` |
| `year` | int | optional — e.g. `2018` |
| `category` | string | optional |
| `limit` / `offset` | int | as above |

```bash
curl -H "X-API-Key: $KEY" \
  "https://autosparefinder.co.il/api/public/v1/fitment?make=Toyota&model=Corolla&year=2018"
```

### `GET /manufacturers`
List of car brands present in the catalog.

## Response schema (all part results)
```json
{
  "part_id": "00437f73-2d26-4ba5-9320-23e9fe136e88",
  "oem_number": "9G33-6714-AA",
  "name": "Oil Filter",
  "name_he": "מסנן שמן",
  "manufacturer": "Jaguar",
  "category": "filters",
  "barcode": null,
  "available": true,
  "price": {
    "amount": 244.82,      // net price (before VAT)
    "vat": 0.0,            // VAT (18% if sourced in Israel, else 0)
    "total": 244.82,       // what the customer pays (excluding shipping)
    "currency": "ILS",
    "vat_included": false
  }
}
```
`price` is `null` and `available` is `false` when no priced stock is currently offered.

## Errors
| Status | Meaning |
|---|---|
| `400` | Missing required params (e.g. neither `q` nor `manufacturer`). |
| `401` | Missing / invalid / inactive API key. |
| `404` | Part not found. |
| `429` | Rate limit exceeded (see your key's per-minute limit). |

## Notes
- Prices reflect the **cheapest currently-available offer** and update as stock/prices change.
- Supplier identities and our internal costs are never exposed.
- Be a good citizen: cache where you can and stay within your rate limit.
