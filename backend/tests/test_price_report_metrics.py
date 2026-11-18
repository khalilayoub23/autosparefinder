from datetime import datetime, timedelta
from types import SimpleNamespace as N
import price_report_metrics as P

NOW = datetime(2026, 11, 19, 7, 0)


def _m(**kw):
    base = {"last_sync": N(created_at=NOW - timedelta(hours=7), parts_updated=11,
                           description="x; synthetic drift disabled; ebay: checked=5 updated=1 no_listing=4 blocked_non_il=0 errors=0 [updated] | aliexpress: disabled"),
            "per_source": {"ebay_sync": (16, NOW - timedelta(hours=1))}, "sync_job": N(status="completed")}
    base.update(kw); return base


def test_full_breakdown_renders_and_catalog_size_is_not_here():
    txt = "\n".join(P.format_lines(_m(offers_estimate=4_800_000, parts_with_price=2_100_000), NOW))
    assert "11 עודכנו" in txt and "checked=5 updated=1" in txt and "ebay_sync: 16" in txt
    assert "aliexpress_sync: 0" in txt and "אין ב-7 ימים" in txt
    assert "חלקים פעילים:" not in txt           # the catalogue-size line lives elsewhere and is not redefined


def test_stale_and_failed_and_unavailable_are_distinguishable():
    assert "מיושן" in "\n".join(P.format_lines(_m(last_sync=N(created_at=NOW - timedelta(hours=60), parts_updated=0, description="")), NOW))
    assert "❌ sync_prices: failed" in "\n".join(P.format_lines(_m(sync_job=N(status="failed")), NOW))
    assert "לא זמינים" in "\n".join(P.format_lines({"last_sync": None}, NOW))
