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
    return (f"{name}: checked={checked} updated={updated} no_listing={not_found} "
            f"blocked_non_il={blocked} errors={errs} [{why}]")


def price_sync_breakdown(ebay_report: Optional[Mapping[str, Any]], aliexpress_report: Optional[Mapping[str, Any]]) -> str:
    return _src("ebay", ebay_report) + " | " + _src("aliexpress", aliexpress_report)
