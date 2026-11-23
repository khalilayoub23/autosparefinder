"""2026-11-20: the exact text of the daily digest is stored (Redis, 14d) so its content can be verified without a test message."""
from pathlib import Path
SRC = (Path(__file__).resolve().parent.parent / "BACKEND_API_ROUTES.py").read_text(encoding="utf-8")


def test_digest_text_is_persisted_after_send():
    i = SRC.index("lines[0] = f\"📅 *AutoSpareFinder — דוח יומי*")
    block = SRC[i:i + 700]
    assert "_wa_send_update" in block and "_store_report_text(\"daily_status\"" in block


def test_store_helper_never_raises_and_expires():
    body = SRC.split("async def _store_report_text", 1)[1].split("_NIGHT_STATE_HE", 1)[0]
    assert "except Exception" in body and "ex=14 * 86400" in body
