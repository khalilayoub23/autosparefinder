from datetime import datetime
from types import SimpleNamespace as NS
from price_report_metrics import format_lines


def test_last_write_is_israel_time_and_label_is_honest():
    m = {"last_sync": NS(created_at=datetime(2026, 11, 23, 0, 22), parts_updated=5, description="x"),
         "per_source": {"ebay_sync": (7, datetime(2026, 11, 23, 0, 11)), "aliexpress_sync": (0, None)},
         "parts_with_price": 10}
    out = "\n".join(format_lines(m, now=datetime(2026, 11, 23, 5, 0)))
    assert "23/11 02:11" in out          # 00:11 UTC = 02:11 Israel (winter, UTC+2)
    assert "אין ב-7 ימים" in out
    assert "מחיר בסיס" in out and "מחיר ללקוח" not in out
