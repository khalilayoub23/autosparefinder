"""
Script: price_sync_report.py
Purpose: One authoritative one-line breakdown of an eBay + AliExpress price-sync run, so a run with few or zero
         updates always says WHY (nothing eligible / checked but no listing / blocked / unchanged / errors).
Process: pure formatting over the two service reports; no DB, no network.
Data Imported/Modified: none (text is stored by the caller in system_logs.message / catalog_versions.description).
Data Sources: services.ebay_price_sync.sync_ebay_prices and services.aliexpress_price_sync.sync_aliexpress_prices reports.
Missing Data Delegation: n/a
Last Updated: 2026-11-18
"""
from typing import Any, Mapping, Optional


def _src(name: str, rep: Optional[Mapping[str, Any]]) -> str:
    rep = rep or {}
    if rep.get("status") == "disabled":
        return f"{name}: disabled"
    checked = int(rep.get("parts_checked") or 0)
    updated = int(rep.get("parts_updated") or 0)
    not_found = int(rep.get("parts_not_found") or 0)
    blocked = int(rep.get("blocked_non_il_shipping") or 0)
    errs = len(rep.get("errors") or [])
    if checked == 0:
        why = "no eligible work found" if not errs else "no work processed (errors)"
    elif updated == 0:
        why = "work found but nothing changed"
    else:
        why = "updated"
    extra = ""
    if rep.get("refresh_verdicts") or rep.get("candidate_verdicts"):
        # Listing Identity Guard (2026-11-19): WHY refreshed/discovered offers were or were not written
        rv = ",".join(f"{k}={v}" for k, v in sorted((rep.get("refresh_verdicts") or {}).items()))
        cv = ",".join(f"{k}={v}" for k, v in sorted((rep.get("candidate_verdicts") or {}).items()))
        extra = (f" refresh[{rv}] discovery[{cv}] calls={rep.get('calls_used')}/{rep.get('call_budget')}"
                 f" quota_at_start={rep.get('ebay_quota_remaining_at_start')}")
    return (f"{name}: checked={checked} updated={updated} no_listing={not_found} "
            f"blocked_non_il={blocked} errors={errs} [{why}]{extra}")


def price_sync_breakdown(ebay_report: Optional[Mapping[str, Any]], aliexpress_report: Optional[Mapping[str, Any]]) -> str:
    return _src("ebay", ebay_report) + " | " + _src("aliexpress", aliexpress_report)
