import os

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from services.supplier_aggregator import ACTIVE_SUPPLIERS, find_best_price, search_all_suppliers, search_by_oem_all
from services.suppliers.aliexpress_supplier import AliExpressSupplier

router = APIRouter(prefix="/api/suppliers", tags=["Suppliers"])


@router.get("/aliexpress/oauth-url")
async def aliexpress_oauth_url():
    """Return the OAuth authorization URL for AliExpress DS."""
    s = AliExpressSupplier()
    return {"url": s.get_oauth_url()}


@router.get("/aliexpress/callback", response_class=HTMLResponse)
async def aliexpress_callback(code: str = Query(None), error: str = Query(None)):
    """OAuth callback — exchanges code for access_token and writes it to .env."""
    if error or not code:
        return HTMLResponse(f"<h2>OAuth Error: {error or 'missing code'}</h2>", status_code=400)

    s = AliExpressSupplier()
    try:
        data = await s.exchange_code_for_token(code)
    except ValueError as e:
        return HTMLResponse(f"<h2>Token exchange failed</h2><pre>{e}</pre>", status_code=502)

    token = data.get("access_token") or data.get("token", {}).get("access_token", "")
    refresh = data.get("refresh_token", "")
    expire = data.get("expire_time", data.get("token_expire", ""))

    # Persist to .env
    env_path = "/app/.env"
    try:
        with open(env_path, "r") as f:
            lines = f.readlines()
        new_lines = []
        wrote = False
        for line in lines:
            if line.startswith("ALIEXPRESS_ACCESS_TOKEN="):
                new_lines.append(f"ALIEXPRESS_ACCESS_TOKEN={token}\n")
                wrote = True
            else:
                new_lines.append(line)
        if not wrote:
            new_lines.append(f"ALIEXPRESS_ACCESS_TOKEN={token}\n")
        with open(env_path, "w") as f:
            f.writelines(new_lines)
        saved = True
    except Exception as ex:
        saved = False

    return HTMLResponse(
        f"""<h2>AliExpress OAuth Complete</h2>
<p><b>access_token:</b> <code>{token[:20]}…</code></p>
<p><b>expire_time:</b> {expire}</p>
<p><b>Saved to .env:</b> {saved}</p>
<p>Full response: <pre>{data}</pre></p>
<p><b>Next step:</b> restart the backend container so the env var is loaded.</p>""",
        status_code=200,
    )


@router.get("/search")
async def search_parts(
    query: str = Query(..., description="Part name or description"),
    limit: int = Query(10, ge=1, le=50),
):
    results = await search_all_suppliers(query, limit)
    return [vars(result) for result in results]


@router.get("/search/oem")
async def search_by_oem(
    oem_number: str = Query(..., description="OEM part number"),
    limit: int = Query(10, ge=1, le=50),
):
    results = await search_by_oem_all(oem_number, limit)
    return [vars(result) for result in results]


@router.get("/compare")
async def compare_prices(
    part: str = Query(..., description="Part name"),
    make: str = Query("", description="Vehicle make"),
    model: str = Query("", description="Vehicle model"),
    year: str = Query("", description="Vehicle year"),
):
    results = await find_best_price(part, make, model, year)
    return [vars(result) for result in results]


@router.get("/health")
async def suppliers_health():
    return {"active_suppliers": [supplier.name for supplier in ACTIVE_SUPPLIERS]}
