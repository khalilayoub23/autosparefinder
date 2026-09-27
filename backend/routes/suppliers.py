import html
import json
import logging
import os
import re
import urllib.parse

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from BACKEND_AUTH_SECURITY import get_current_admin_user
from services.supplier_aggregator import ACTIVE_SUPPLIERS, find_best_price, search_all_suppliers, search_by_oem_all
from services.suppliers.aliexpress_supplier import AliExpressSupplier

router = APIRouter(prefix="/api/suppliers", tags=["Suppliers"])


class _RedactOAuthQuery(logging.Filter):
    """uvicorn's access log prints the full request line, i.e. `GET /api/aliexpress/callback?code=<auth code>`.
    Redact OAuth values on the AliExpress callback paths so a code never reaches docker logs."""
    _RX = re.compile(r"(code|state|access_token|refresh_token)=[^&\s\"]*")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    self._RX.sub(r"\1=<redacted>", a) if isinstance(a, str) and "aliexpress/callback" in a else a
                    for a in record.args)
        except Exception:
            pass
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactOAuthQuery())


@router.get("/aliexpress/oauth-url")
async def aliexpress_oauth_url():
    """Return the OAuth authorization URL for AliExpress DS."""
    s = AliExpressSupplier()
    return {"url": s.get_oauth_url()}


async def _oauth_params(request: Request) -> tuple:
    """(code, error) from the query string, else from a form/JSON body (AliExpress may POST). Never logged."""
    q = request.query_params
    code, error = q.get("code"), q.get("error")
    if request.method == "POST" and not code and not error:
        try:
            raw = (await request.body())[:65536]
            if "json" in (request.headers.get("content-type") or "").lower():
                body = json.loads(raw or b"{}")
                body = body if isinstance(body, dict) else {}
            else:
                body = {k: v[0] for k, v in urllib.parse.parse_qs(raw.decode("utf-8", "ignore")).items()}
            code, error = body.get("code"), body.get("error")
        except Exception:
            pass
    return code, error


async def aliexpress_callback_request(request: Request) -> HTMLResponse:
    """Single entry point for BOTH registered callback paths (GET or POST)."""
    code, error = await _oauth_params(request)
    return await aliexpress_callback(code=code, error=error)


@router.get("/aliexpress/callback", response_class=HTMLResponse)
async def aliexpress_callback_get(request: Request):
    return await aliexpress_callback_request(request)


@router.post("/aliexpress/callback", response_class=HTMLResponse)
async def aliexpress_callback_post(request: Request):
    return await aliexpress_callback_request(request)


async def aliexpress_callback(code: str = Query(None), error: str = Query(None)):
    """OAuth callback core — exchanges code for tokens and stores them encrypted on the AliExpress supplier row."""
    if error or not code:
        return HTMLResponse(f"<h2>OAuth Error: {html.escape(error or 'missing code')}</h2>", status_code=400)

    s = AliExpressSupplier()
    try:
        data = await s.exchange_code_for_token(code)
    except ValueError as e:
        return HTMLResponse(f"<h2>Token exchange failed</h2><pre>{html.escape(str(e))[:300]}</pre>", status_code=502)
    except Exception as e:  # network / HTTP-status errors: type only, never the payload
        return HTMLResponse(f"<h2>Token exchange failed</h2><p>{html.escape(type(e).__name__)}</p>", status_code=502)

    token = data.get("access_token") or data.get("token", {}).get("access_token", "")
    refresh = data.get("refresh_token", "")
    expire = data.get("expire_time", data.get("token_expire", ""))

    if not token or not refresh:
        return HTMLResponse("<h2>Token exchange returned no usable tokens</h2>", status_code=502)
    try:
        await s.persist_tokens(token, refresh, expire)
    except Exception as ex:
        return HTMLResponse(f"<h2>Authorized, but token storage failed</h2><p>{type(ex).__name__}</p>", status_code=500)

    return HTMLResponse(
        f"""<h2>AliExpress OAuth Complete</h2>
<p><b>access_token:</b> stored (encrypted)</p>
<p><b>refresh_token:</b> stored (encrypted)</p>
<p><b>expire_time:</b> {expire}</p>
<p>Tokens are stored in the database and loaded automatically — no restart needed.</p>""",
        status_code=200,
    )


# RAW supplier results (cost, supplier name, seller, item URL): internal diagnostics — ADMIN ONLY. They were
# unauthenticated until 2026-09-21, which exposed raw supplier costs (incl. AliExpress) to anyone.
@router.get("/search")
async def search_parts(
    query: str = Query(..., description="Part name or description"),
    limit: int = Query(10, ge=1, le=50),
    _admin=Depends(get_current_admin_user),
):
    results = await search_all_suppliers(query, limit)
    return [vars(result) for result in results]


@router.get("/search/oem")
async def search_by_oem(
    oem_number: str = Query(..., description="OEM part number"),
    limit: int = Query(10, ge=1, le=50),
    _admin=Depends(get_current_admin_user),
):
    results = await search_by_oem_all(oem_number, limit)
    return [vars(result) for result in results]


@router.get("/compare")
async def compare_prices(
    part: str = Query(..., description="Part name"),
    make: str = Query("", description="Vehicle make"),
    model: str = Query("", description="Vehicle model"),
    year: str = Query("", description="Vehicle year"),
    _admin=Depends(get_current_admin_user),
):
    results = await find_best_price(part, make, model, year)
    return [vars(result) for result in results]


@router.get("/health")
async def suppliers_health():
    return {"active_suppliers": [supplier.name for supplier in ACTIVE_SUPPLIERS]}
