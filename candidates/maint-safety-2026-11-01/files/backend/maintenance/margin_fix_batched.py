#!/usr/bin/env python3
"""
Script: margin_fix_batched.py
Purpose: SUPERSEDED (#119, 2026-11-01). It rewrote `base_price = ROUND(importer_price_ils * 1.45, 2)` for every part whose
         base_price differed, i.e. over parts that own a customer-visible offer, where base_price is the net of the
         cheapest eligible offer, not of the importer cost. base_price convergence is owned by the guarded tasks
         fix_base_prices / normalize_base_price (db_update_agent.apply_base_price_rule over price_policy.py).
Process: delegates to price_audit_fix.py (read-only dry run by default, --apply = the guarded tasks).
Data Imported/Modified: none directly.
Last Updated: 2026-11-01
"""
import asyncio
import sys

import price_audit_fix

if __name__ == "__main__":
    print("[margin_fix] superseded: delegating to price_audit_fix (dry run unless --apply)", flush=True)
    sys.exit(asyncio.run(price_audit_fix.main(sys.argv[1:])))
