"""
Script: ebay_api_watch.py
Purpose: Early warning that eBay is retiring / changing an endpoint we depend on
         (Buy Browse API: item_summary/search, item/{id}), so a retirement does
         not show up as a silent "0 results" in the supplier search / price sync.
Process: ebay_supplier, ebay_brand_importer (async `check`) and
         external_fitment_providers.classify_external_payload (sync `check_sync`) call `await check(endpoint, response)` on every Browse
         response BEFORE raise_for_status(). A signal is (a) a Deprecation /
         Sunset / Warning header, (b) a redirect, 404, 410 or 501 on the
         endpoint, or (c) a 4xx body that says deprecated / decommissioned /
         retired / no longer. Any signal -> ONE owner alert through
         BACKEND_API_ROUTES.notify_owner (quiet-hours safe), de-duplicated by
         fingerprint, reminder every 7 days. Never raises, never blocks a call.
Data Imported/Modified: none (Redis alert state is handled by notify_owner).
Data Sources: eBay Browse API response status / headers / body.
Missing Data Delegation: n/a
Last Updated: 2026-11-18
Context: eBay announced Modernized API GA on 2026-10-27; verified 2026-11-17 that the
         official deprecation page lists NO Browse entry (Finding/Shopping were
         decommissioned 2025-02-04 and replaced BY Browse). This is the tripwire
         in case that changes.
"""
import asyncio
import hashlib
import logging
import re
import time

logger = logging.getLogger(__name__)

_HEADER_SIGNALS = ("deprecation", "sunset", "warning")
_STATUS_SIGNALS = {301, 302, 307, 308, 404, 410, 501}
_THROTTLE_S = 3600  # in-process: one alert attempt per (endpoint, reason) per hour (notify_owner de-dupes across processes)
_last_alert: dict = {}
_pending_tasks: set = set()
_BODY_RE = re.compile(r"deprecat|decommission|retired|no longer (?:available|supported)", re.I)


def detect(response, endpoint: str = "") -> str:
    """Return a short human reason if the response signals a retirement, else ''.
    404/410 on item/{id} is an ENDED LISTING (normal), so those only count on search."""
    try:
        status = int(response.status_code)
        for h in _HEADER_SIGNALS:
            val = response.headers.get(h)
            if val:
                return f"header {h}: {str(val)[:160]}"
        if status in _STATUS_SIGNALS and not (endpoint == "browse/item" and status in (404, 410)):
            return f"HTTP {status}"
        if 400 <= status < 500 and status != 429:
            m = _BODY_RE.search((response.text or "")[:2000])
            if m:
                return f"HTTP {status} body mentions '{m.group(0)}'"
    except Exception:  # a watcher must never break the caller
        return ""
    return ""


async def _alert(endpoint: str, reason: str) -> None:
    """Log + owner alert for one detected signal. Never raises."""
    key = (endpoint, reason)
    now = time.monotonic()
    if now - _last_alert.get(key, -_THROTTLE_S) < _THROTTLE_S:
        return
    _last_alert[key] = now
    logger.error("eBay API change signal on %s: %s", endpoint, reason)
    try:
        from BACKEND_API_ROUTES import notify_owner

        fp = hashlib.sha256(f"{endpoint}|{reason}".encode()).hexdigest()[:16]
        await notify_owner(
            "supplier",
            f"eBay API change signal: {endpoint}",
            f"{reason}\nCheck https://developer.ebay.com/develop/get-started/api-deprecation-status "
            "- the eBay price sync / search may stop returning results.",
            severity="warning",
            alert_key=f"ebay_api_watch:{endpoint}",
            fingerprint=fp,
            remind_s=7 * 24 * 3600,
        )
    except Exception as exc:
        logger.error("ebay_api_watch alert failed: %s", exc)


async def check(endpoint: str, response) -> None:
    """Alert the owner once per distinct (endpoint, reason). Never raises."""
    try:
        reason = detect(response, endpoint)
        if reason:
            await _alert(endpoint, reason)
    except Exception as exc:  # a watcher must never break the caller
        logger.error("ebay_api_watch check failed: %s", exc)


def check_sync(endpoint: str, response) -> None:
    """Same inspection for SYNC call sites (e.g. external_fitment_providers.classify_external_payload).
    With a running event loop the alert is scheduled as a task; without one (CLI pass) the
    signal is logged at ERROR only. Never raises, never blocks."""
    try:
        reason = detect(response, endpoint)
        if not reason:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error("eBay API change signal on %s: %s (no event loop - owner alert skipped)", endpoint, reason)
            return
        task = loop.create_task(_alert(endpoint, reason))
        _pending_tasks.add(task)
        task.add_done_callback(_pending_tasks.discard)
    except Exception as exc:
        logger.error("ebay_api_watch check_sync failed: %s", exc)
