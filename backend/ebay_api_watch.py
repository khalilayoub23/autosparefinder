"""
Script: ebay_api_watch.py
Purpose: Early warning that eBay is retiring / changing an endpoint we depend on
         (Buy Browse API: item_summary/search, item/{id}), so a retirement does
         not show up as a silent "0 results" in the supplier search / price sync.
Process: ebay_supplier calls `await check(endpoint, response)` on every Browse
         response BEFORE raise_for_status(). A signal is (a) a Deprecation /
         Sunset / Warning header, (b) a redirect, 404, 410 or 501 on the
         endpoint, or (c) a 4xx body that says deprecated / decommissioned /
         retired / no longer. Any signal -> ONE owner alert through
         BACKEND_API_ROUTES.notify_owner (quiet-hours safe), de-duplicated by
         fingerprint, reminder every 7 days. Never raises, never blocks a call.
Data Imported/Modified: none (Redis alert state is handled by notify_owner).
Data Sources: eBay Browse API response status / headers / body.
Missing Data Delegation: n/a
Last Updated: 2026-11-17
Context: eBay announced Modernized API GA on 2026-10-27; verified 2026-11-17 that the
         official deprecation page lists NO Browse entry (Finding/Shopping were
         decommissioned 2025-02-04 and replaced BY Browse). This is the tripwire
         in case that changes.
"""
import hashlib
import logging
import re

logger = logging.getLogger(__name__)

_HEADER_SIGNALS = ("deprecation", "sunset", "warning")
_STATUS_SIGNALS = {301, 302, 307, 308, 404, 410, 501}
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


async def check(endpoint: str, response) -> None:
    """Alert the owner once per distinct (endpoint, reason). Never raises."""
    reason = detect(response, endpoint)
    if not reason:
        return
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
