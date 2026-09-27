"""AliExpress OEM matching regression suite (no network): normalization, response parsing, candidate
extraction/rejection, valid vs ambiguous matches, numeric-OEM routing, junk-OEM safety.

Response fixtures use the REAL structure observed from aliexpress.ds.text.search on 2026-09-21
(aliexpress_ds_text_search_response.data.products.selection_search_product[*]: itemId, title,
targetSalePrice, salePrice, originalPrice, itemMainPic, ...). Real titles below were seen live.
"""
from unittest.mock import AsyncMock

import httpx
import pytest

import services.suppliers.aliexpress_supplier as m
from services.suppliers.aliexpress_supplier import AliExpressSupplier

REAL_TITLE_MULTI_OE = "15400-RTA-004 15400-PLC-003 15400-PLM-A01 15400-RBA-F01 Engine Oil Filter For Honda Civic"
REAL_TITLE_JUNK_LOT = "20pcs/lot MG655814-5 PB621-04720-1 W12S 1-962919-1 3216 connector terminals"
REAL_TITLE_YEARS = "Motorcycle Front and Rear Brake Pads for HONDA CBF 600 SA CBF 500 2007-2008"


@pytest.fixture
def sup(monkeypatch):
    for k, v in (("ALIEXPRESS_APP_KEY", "546482"), ("ALIEXPRESS_APP_SECRET", "S" * 32), ("ENCRYPTION_KEY", "k" * 64)):
        monkeypatch.setenv(k, v)
    s = AliExpressSupplier()
    monkeypatch.setattr(s, "_ensure_token", AsyncMock(return_value=True))
    return s


def item(item_id="1005001234567890", title=REAL_TITLE_MULTI_OE, price="10.06"):
    return {"itemId": item_id, "title": title, "targetSalePrice": price, "salePrice": price,
            "originalPrice": "14.10" if price != "0" else "0",
            "itemMainPic": "//ae01.alicdn.com/kf/abc.jpg", "score": "4.8", "cateId": "1", "discount": "29%"}


# ---- OEM normalization -------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,norm", [("15400-PLM-A01", "15400PLMA01"), ("11 42 8 507 694", "114285076" "94"),
                                       (" 3132114020 ", "3132114020"), ("bracket-c2d39192", "BRACKETC2D39192"), (None, "")])
def test_oem_normalization(raw, norm):
    assert AliExpressSupplier._norm_oem(raw) == norm


# ---- guard: valid / invalid / ambiguous --------------------------------------------------------------
@pytest.mark.parametrize("oem", ["15400-PLM-A01", "15400PLMA01", "15400-PLMA01", "154 00PLMA01".replace(" ", "")])
def test_valid_oem_match_multi_cross_reference_title(oem):
    assert AliExpressSupplier._oem_matches_title(oem, REAL_TITLE_MULTI_OE)

@pytest.mark.parametrize("oem,title", [
    ("12345678", "X12345678Y bearing"),                    # embedded in a longer alphanumeric run
    ("12345678", "part 123456789 kit"),                    # OEM is a prefix of a longer number
    ("A1234", "A1234 filter"),                             # < 8 chars: too collision-prone, never guessed
    ("15400-PLM-A02", REAL_TITLE_MULTI_OE),                # sibling OEM (A01 listed, A02 wanted)
    ("11428507694", REAL_TITLE_JUNK_LOT),                  # unrelated fuzzy hit
    ("2007-2008", REAL_TITLE_YEARS),                       # junk catalog "OEM" = model-year range
    ("2009-2010", "Brake pads 2009-2010 fit Dacia"),
    ("12345678", "Brake pad AB-12345678 kit"),             # glued to another number through a hyphen
    ("12345678", "Brake pad 12345678-99 kit"),
])
def test_invalid_or_ambiguous_match_rejected(oem, title):
    assert not AliExpressSupplier._oem_matches_title(oem, title)


@pytest.mark.parametrize("oem,title", [
    ("3132114020", "2Pcs Brake Clutch Pedal Pad Cover For Toyota Camry 31321-14020 Rubber"),    # catalog no-dash, title dashed
    ("31321-14020", "Clutch pedal pad 3132114020 Toyota"),                                        # catalog dashed, title no-dash
    ("11 42 8 507 694", "Oil filter 11 42 8 507 694 BMW"),                                        # catalog spaced form
    ("15400-PLM-A01", "Oil Filter 15400 PLM A01 Honda"),                                          # title spaces for dashes
])
def test_hyphen_and_spacing_style_differences_still_match(oem, title):
    assert AliExpressSupplier._oem_matches_title(oem, title)


# ---- text_search response parsing --------------------------------------------------------------------
class _R:
    status_code = 200
    def __init__(self, d): self._d = d
    def json(self): return self._d
    def raise_for_status(self): pass

def _post(monkeypatch, payload, capture=None):
    async def post(self, url, data=None, **kw):
        if capture is not None:
            capture.append((url, dict(data or {})))
        return _R(payload)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)

async def test_text_search_parses_real_structure(sup, monkeypatch):
    sent = []
    _post(monkeypatch, {"aliexpress_ds_text_search_response": {"data": {"products": {"selection_search_product": [item(), item("2")]}}}}, sent)
    out = await sup.text_search("15400-PLM-A01", limit=30)
    assert [p["itemId"] for p in out] == ["1005001234567890", "2"]
    url, params = sent[0]
    assert url == "https://api-sg.aliexpress.com/sync" and params["method"] == "aliexpress.ds.text.search"
    assert params["keyWord"] == "15400-PLM-A01" and params["pageSize"] == "30" and params["access_token"] is not None

async def test_text_search_error_response_returns_empty(sup, monkeypatch):
    _post(monkeypatch, {"error_response": {"code": "IllegalAccessToken", "msg": "x"}})
    assert await sup.text_search("15400-PLM-A01") == []

async def test_text_search_empty_selection_returns_empty(sup, monkeypatch):
    _post(monkeypatch, {"aliexpress_ds_text_search_response": {"data": {"products": {}}}})
    assert await sup.text_search("15400-PLM-A01") == []


# ---- candidate extraction / rejection through the production guarded search ---------------------------
async def test_guarded_search_accepts_only_oem_bearing_candidate_and_extracts_fields(sup, monkeypatch):
    monkeypatch.setattr(sup, "text_search", AsyncMock(return_value=[
        item("111", REAL_TITLE_JUNK_LOT, "0.50"),          # rejected: OEM absent
        item("222", REAL_TITLE_MULTI_OE, "10.06"),         # accepted
        item("333", "Oil Filter 15400-PLM-A02", "8.00"),   # rejected: sibling OEM
        item("444", REAL_TITLE_MULTI_OE, "0"),             # rejected: no price
    ]))
    res = await sup.search_by_oem("15400-PLM-A01", limit=3)
    assert [r.item_id for r in res] == ["222"]
    r = res[0]
    assert r.price == 10.06 and r.currency == "USD" and r.total_cost == 10.06
    assert r.item_url == "https://www.aliexpress.com/item/222.html" and r.image_urls == ["//ae01.alicdn.com/kf/abc.jpg"]
    assert r.tech_specs["oem_verified"] is True and r.shipping_cost == 0.0     # shipping NOT invented

async def test_guarded_search_no_candidate_when_none_carry_oem(sup, monkeypatch):
    monkeypatch.setattr(sup, "text_search", AsyncMock(return_value=[item("1", REAL_TITLE_JUNK_LOT)]))
    assert await sup.search_by_oem("11428507694") == []

async def test_short_oem_never_queries_api(sup, monkeypatch):
    ts = AsyncMock(return_value=[item()]); monkeypatch.setattr(sup, "text_search", ts)
    assert await sup.search_by_oem("A1234") == [] and ts.await_count == 0


# ---- numeric OEM routing (root-cause defect) ----------------------------------------------------------
@pytest.mark.parametrize("oem", ["51427485762", "3510660140", "8475538000", "99163192600"])   # real backlog OEMs (BMW/Toyota/Kia/Porsche)
async def test_numeric_oem_uses_keyword_search_not_product_id_fetch(sup, monkeypatch, oem):
    ts = AsyncMock(return_value=[]); fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(sup, "text_search", ts); monkeypatch.setattr(sup, "search", fetch)
    await sup.search_by_oem(oem, limit=3)
    assert ts.await_count == 1 and fetch.await_count == 0        # was: product-id path, keyword search never ran

async def test_product_id_fetch_still_available_explicitly(sup, monkeypatch):
    sent = []
    _post(monkeypatch, {"aliexpress_ds_product_wholesale_get_response": {"result": {}}}, sent)
    await sup.search("1005001234567890")
    assert sent and sent[0][1]["method"] == "aliexpress.ds.product.wholesale.get"


# ---- query construction (root cause: bare part numbers get fuzzy, unrelated results) -------------------
def test_query_is_brand_name_oem_and_tolerates_missing_parts():
    q = AliExpressSupplier._build_oem_query
    assert q("11428507694", "BMW", "Oil Filter Element") == "BMW Oil Filter Element 11428507694"
    assert q("15400-PLM-A01", "", "") == "15400-PLM-A01"
    assert q(" 3132114020 ", "  Toyota ", "") == "Toyota 3132114020"
    assert q("X1234567", "Kia", "S/S to Gxd7071cb (Rear) ---") == "Kia S S to Gxd7071cb Rear X1234567"   # junk punctuation stripped

def test_query_truncates_only_the_name_never_the_oem():
    long_name = "Engine Timing Camshaft Sprocket Assembly With Extra Descriptive Words That Go On And On Forever"
    out = AliExpressSupplier._build_oem_query("13520WB001", "Toyota", long_name)
    assert out.startswith("Toyota Engine Timing") and out.endswith(" 13520WB001") and len(out) <= len("Toyota ") + 60 + len(" 13520WB001")

async def test_search_sends_brand_query_but_acceptance_ignores_brand(sup, monkeypatch):
    ts = AsyncMock(return_value=[item("9", "Genuine BMW brake pads set fits many models")])   # brand in title, OEM absent
    monkeypatch.setattr(sup, "text_search", ts)
    res = await sup.search_by_oem("11428507694", limit=3, brand="BMW", name="Brake Pad")
    assert ts.await_args.args[0] == "BMW Brake Pad 11428507694"
    assert res == []                                          # brand match alone NEVER accepts a candidate

async def test_customer_oem_search_signature_unchanged(sup, monkeypatch):
    ts = AsyncMock(return_value=[]); monkeypatch.setattr(sup, "text_search", ts)
    await sup.search_by_oem("11428507694", 5)                 # aggregator call style: positional, no brand
    assert ts.await_args.args[0] == "11428507694"


# ---- unit-price integrity: multi-pack listings must never become a part's unit cost -----------------------
@pytest.mark.parametrize("title", [
    "Three (3) Oil Filter 04152-YZZA6 For Toyota Prius",      # real listings seen 2026-09-21
    "5X Engine Oil Filter For Toyota LEXUS Prius Scion 04152-YZZA7",
    "Y93A-6Sets Oil Filter Kits For Toyota Avalon Camry 04152-YZZA1",
    "2Pcs Brake Clutch Pedal Pad Cover Set For Toyota Camry 31321-14020",
    "1/2/3/5Pcs Brake Pedal Stopper Damper 32876-36000",
    "20pcs/lot Oil Filter 15400-PLM-A01",
    "Oil Filter 15400-PLM-A01 wholesale bulk",
    "4 Pieces Oil Filter 15400-PLM-A01",
])
def test_multi_pack_listing_flagged(title):
    assert AliExpressSupplier._multi_unit_reason(title)

@pytest.mark.parametrize("title", [
    "Engine Oil Filter For Honda Civic 15400-PLM-A01",
    "Brake Pad Set Front 04465-02220 Toyota",                # 'Set' without a quantity is the sold unit
    "1 Piece Oil Filter 15400-PLM-A01",                      # quantity 1
    "Oil Filter Kit With Gasket 15400-PLM-A01",
    "Bolt M12 x 1.5 flange 90119-12345 Toyota",              # dimension spec, not a pack
    "Tyre 225/50R16 4x4 all terrain",
])
def test_single_unit_listing_not_flagged(title):
    assert AliExpressSupplier._multi_unit_reason(title) is None

async def test_multi_pack_candidate_rejected_even_when_oem_matches(sup, monkeypatch):
    monkeypatch.setattr(sup, "text_search", AsyncMock(return_value=[
        item("1", "Three (3) Oil Filter 04152-YZZA6 For Toyota Prius", "10.23"),
        item("2", "Oil Filter 04152-YZZA6 For Toyota Prius", "4.10"),
    ]))
    res = await sup.search_by_oem("04152YZZA6", limit=3, brand="Toyota")
    assert [r.item_id for r in res] == ["2"] and res[0].price == 4.10                # pack rejected, unit accepted


# ---- ApiCallLimit observability (2026-09-24) ------------------------------------------------------
# Root cause: every error_response branch logged generically, so a real AliExpress-side flow-control
# rejection (code "ApiCallLimit", observed live 2026-09-22 on aliexpress.ds.product.get) was
# indistinguishable from any other business error (e.g. IllegalAccessToken) and invisible to a run's
# report — `report["errors"]` only ever saw raised exceptions, never a clean error_response return.
# _classify_api_error() is the single point of truth; these tests prove it counts ApiCallLimit and
# ONLY ApiCallLimit, without changing any caller's existing return value / fallback behavior.

async def test_text_search_api_call_limit_increments_counter(sup, monkeypatch):
    assert sup.api_call_limit_hits == 0
    _post(monkeypatch, {"error_response": {"code": "ApiCallLimit", "msg": "Api access frequency exceeds the limit. this ban will last 1 seconds"}})
    out = await sup.text_search("15400-PLM-A01")
    assert out == []                              # unchanged fallback: empty list, no exception, no retry
    assert sup.api_call_limit_hits == 1


async def test_text_search_unrelated_error_does_not_increment_counter(sup, monkeypatch):
    _post(monkeypatch, {"error_response": {"code": "IllegalAccessToken", "msg": "x"}})
    out = await sup.text_search("15400-PLM-A01")
    assert out == [] and sup.api_call_limit_hits == 0     # classified as before: a generic warning, not a limit hit


async def test_text_search_success_does_not_increment_counter(sup, monkeypatch):
    _post(monkeypatch, {"aliexpress_ds_text_search_response": {"data": {"products": {"selection_search_product": [item()]}}}})
    out = await sup.text_search("15400-PLM-A01")
    assert len(out) == 1 and sup.api_call_limit_hits == 0


async def test_get_part_details_api_call_limit_increments_counter_and_returns_none(sup, monkeypatch):
    assert sup.api_call_limit_hits == 0
    _post(monkeypatch, {"error_response": {"code": "ApiCallLimit", "msg": "Api access frequency exceeds the limit. this ban will last 1 seconds"}})
    out = await sup.get_part_details("1005001234567890")
    assert out is None                            # unchanged fallback: caller falls back to the listing-level result
    assert sup.api_call_limit_hits == 1


async def test_get_part_details_unrelated_error_does_not_increment_counter(sup, monkeypatch):
    _post(monkeypatch, {"error_response": {"code": "System Error", "msg": "x"}})
    out = await sup.get_part_details("1005001234567890")
    assert out is None and sup.api_call_limit_hits == 0


async def test_search_wholesale_item_not_found_still_debug_logged_not_counted(sup, monkeypatch):
    """ITEM_ID_NOT_FOUND-style codes (15/27) keep their existing special-cased debug path — never
    counted as an ApiCallLimit hit and never demoted to a generic warning."""
    _post(monkeypatch, {"error_response": {"code": "15", "msg": "ITEM_ID_NOT_FOUND"}})
    out = await sup.search("1005001234567890")
    assert out == [] and sup.api_call_limit_hits == 0


async def test_search_wholesale_api_call_limit_is_counted(sup, monkeypatch):
    _post(monkeypatch, {"error_response": {"code": "ApiCallLimit", "msg": "Api access frequency exceeds the limit. this ban will last 1 seconds"}})
    out = await sup.search("1005001234567890")
    assert out == [] and sup.api_call_limit_hits == 1


async def test_two_consecutive_hits_accumulate_the_counter():
    """The counter is per-instance and monotonic across multiple calls in one run — proves 'count' in
    the log line and the run-level report both reflect a real running total, not a boolean flag."""
    import services.suppliers.aliexpress_supplier as m
    s = m.AliExpressSupplier()
    s._classify_api_error("aliexpress.ds.text.search", {"code": "ApiCallLimit", "msg": "a"})
    s._classify_api_error("aliexpress.ds.product.get", {"code": "ApiCallLimit", "msg": "b"})
    s._classify_api_error("aliexpress.ds.text.search", {"code": "IllegalAccessToken", "msg": "c"})
    assert s.api_call_limit_hits == 2


# ---- transient-failure vs confirmed-negative (2026-09-26 remediation) ---------------------------------
# Root cause: text_search swallowed EVERY failure into [] — indistinguishable from "AliExpress answered: nothing
# found" — so 3 transport timeouts in the 2,000-target validation series were recorded as a confirmed `no_match`
# (30-day negative cache). Fix: raise_on_unconfirmed=True raises AliExpressSearchUnconfirmed(kind) for a search
# that got NO answer; the default contract (customer search, discovery) still never raises. Transport errors get
# at most ONE retry through the project's existing resilience.retry_with_backoff; flow control / auth / API errors
# are never retried. All scripted below with deterministic mocks — no network, no quota use.
import resilience
from services.suppliers.aliexpress_supplier import AliExpressSearchUnconfirmed, _error_kind


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr(resilience.asyncio, "sleep", AsyncMock())         # the retry helper's backoff sleep


def _script(monkeypatch, steps, calls):
    """Scripted httpx.AsyncClient.post. Each step: payload dict (HTTP 200) | Exception (raised) | ("status", code).
    The LAST step repeats forever, so a missing retry bound shows up as an over-count, never as a hang."""
    async def post(self, url, data=None, **kw):
        calls.append(dict(data or {}))
        step = steps[min(len(calls) - 1, len(steps) - 1)]
        if isinstance(step, Exception):
            raise step
        if isinstance(step, tuple) and step[0] == "status":
            req = httpx.Request("POST", url)
            resp = httpx.Response(step[1], request=req)
            class _Bad:
                status_code = step[1]
                def json(self): return {}
                def raise_for_status(self): raise httpx.HTTPStatusError("bad status", request=req, response=resp)
            return _Bad()
        return _R(step)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)


OK_ONE = {"aliexpress_ds_text_search_response": {"data": {"products": {"selection_search_product": [item()]}}}}
OK_EMPTY = {"aliexpress_ds_text_search_response": {"data": {"products": {}}}}


async def test_default_contract_unchanged_failures_still_return_empty_never_raise_and_never_retry(sup, monkeypatch, no_backoff):
    calls = []
    _script(monkeypatch, [httpx.ReadTimeout("")], calls)
    assert await sup.text_search("15400-PLM-A01") == []                       # customer search / discovery keep this
    assert len(calls) == 1                                                    # ... with their original SINGLE attempt (latency unchanged)
    assert await sup.search_by_oem("15400-PLM-A01", limit=3) == []
    assert len(calls) == 2 and sup.api_calls == 2                             # one attempt per call, no retry


async def test_timeout_is_unconfirmed_after_exactly_one_bounded_retry(sup, monkeypatch, no_backoff):
    calls = []
    _script(monkeypatch, [httpx.ReadTimeout("")], calls)                      # persistent timeout
    with pytest.raises(AliExpressSearchUnconfirmed) as ei:
        await sup.text_search("15400-PLM-A01", raise_on_unconfirmed=True)
    assert ei.value.kind == "transport" and ei.value.transient and "ReadTimeout" in ei.value.detail
    assert len(calls) == 2 and sup.api_calls == 2                              # 1 attempt + 1 retry, then it STOPS
    assert sup.api_call_limit_hits == 0


async def test_transient_failure_then_success_recovers_via_the_bounded_retry(sup, monkeypatch, no_backoff):
    calls = []
    _script(monkeypatch, [httpx.ConnectTimeout(""), OK_ONE], calls)
    out = await sup.text_search("15400-PLM-A01", raise_on_unconfirmed=True)
    assert len(out) == 1 and len(calls) == 2


async def test_http_503_is_retried_once_then_unconfirmed(sup, monkeypatch, no_backoff):
    calls = []
    _script(monkeypatch, [("status", 503)], calls)
    with pytest.raises(AliExpressSearchUnconfirmed) as ei:
        await sup.text_search("15400-PLM-A01", raise_on_unconfirmed=True)
    assert ei.value.kind == "http_status" and ei.value.transient and "503" in ei.value.detail and len(calls) == 2


async def test_http_401_is_auth_and_is_not_retried(sup, monkeypatch, no_backoff):
    calls = []
    _script(monkeypatch, [("status", 401)], calls)
    with pytest.raises(AliExpressSearchUnconfirmed) as ei:
        await sup.text_search("15400-PLM-A01", raise_on_unconfirmed=True)
    assert ei.value.kind == "auth" and not ei.value.transient and len(calls) == 1


async def test_api_call_limit_is_rate_limit_unconfirmed_counted_and_never_retried(sup, monkeypatch, no_backoff):
    calls = []
    _script(monkeypatch, [{"error_response": {"code": "ApiCallLimit", "msg": "Api access frequency exceeds the limit. this ban will last 1 seconds"}}], calls)
    with pytest.raises(AliExpressSearchUnconfirmed) as ei:
        await sup.text_search("15400-PLM-A01", raise_on_unconfirmed=True)
    assert ei.value.kind == "rate_limit" and ei.value.transient
    assert len(calls) == 1 and sup.api_call_limit_hits == 1                    # flow control is not hammered; still observable


async def test_expired_token_response_is_auth_unconfirmed_not_retried(sup, monkeypatch, no_backoff):
    calls = []
    _script(monkeypatch, [{"error_response": {"code": "IllegalAccessToken", "msg": "The specified access token is invalid or expired"}}], calls)
    with pytest.raises(AliExpressSearchUnconfirmed) as ei:
        await sup.text_search("15400-PLM-A01", raise_on_unconfirmed=True)
    assert ei.value.kind == "auth" and not ei.value.transient and len(calls) == 1 and sup.api_call_limit_hits == 0


async def test_other_api_error_is_api_error_unconfirmed_not_retried(sup, monkeypatch, no_backoff):
    calls = []
    _script(monkeypatch, [{"error_response": {"code": "MissingParameter", "msg": "x"}}], calls)
    with pytest.raises(AliExpressSearchUnconfirmed) as ei:
        await sup.text_search("15400-PLM-A01", raise_on_unconfirmed=True)
    assert ei.value.kind == "api_error" and not ei.value.transient and len(calls) == 1


async def test_malformed_body_is_unconfirmed(sup, monkeypatch, no_backoff):
    _script(monkeypatch, [[]], [])                                            # a JSON array, not an object
    with pytest.raises(AliExpressSearchUnconfirmed) as ei:
        await sup.text_search("15400-PLM-A01", raise_on_unconfirmed=True)
    assert ei.value.kind == "api_error"


async def test_empty_successful_answer_is_a_real_negative_and_does_not_raise(sup, monkeypatch, no_backoff):
    calls = []
    _script(monkeypatch, [OK_EMPTY], calls)
    assert await sup.text_search("15400-PLM-A01", raise_on_unconfirmed=True) == []   # answered: nothing found
    assert len(calls) == 1


async def test_missing_credentials_is_auth_unconfirmed_only_when_asked(sup, monkeypatch, no_backoff):
    monkeypatch.setattr(sup, "_ensure_token", AsyncMock(return_value=False))
    calls = []
    _script(monkeypatch, [OK_ONE], calls)
    assert await sup.text_search("15400-PLM-A01") == []                       # default contract unchanged
    with pytest.raises(AliExpressSearchUnconfirmed) as ei:
        await sup.text_search("15400-PLM-A01", raise_on_unconfirmed=True)
    assert ei.value.kind == "auth" and calls == []                            # and no HTTP call was made


async def test_search_by_oem_propagates_unconfirmed_and_keeps_local_negatives(sup, monkeypatch, no_backoff):
    calls = []
    _script(monkeypatch, [httpx.ReadTimeout("")], calls)
    assert await sup.search_by_oem("15400-PLM-A01", limit=3) == []            # default: swallow, as before
    with pytest.raises(AliExpressSearchUnconfirmed):
        await sup.search_by_oem("15400-PLM-A01", limit=3, raise_on_unconfirmed=True)
    n = len(calls)
    assert await sup.search_by_oem("A1234", limit=3, raise_on_unconfirmed=True) == []   # short OEM: local decision, no API call
    assert len(calls) == n


@pytest.mark.parametrize("code,kind", [("ApiCallLimit", "rate_limit"), ("IllegalAccessToken", "auth"), ("IllegalRefreshToken", "auth"),
                                       ("InvalidSignature", "auth"), ("InvalidAppKey", "auth"), ("ITEM_ID_NOT_FOUND", "api_error"),
                                       ("MissingParameter", "api_error"), ("InvalidApiPath", "api_error"), ("", "api_error")])
def test_error_code_classification(code, kind):
    assert _error_kind(code) == kind
