"""ebay_api_watch: detects Browse API retirement signals, ignores normal responses (no DB, no network)."""
import ebay_api_watch as w


class R:
    def __init__(self, status=200, headers=None, text=""):
        self.status_code, self.headers, self.text = status, headers or {}, text


def test_normal_and_rate_limit_are_not_signals():
    assert w.detect(R(200)) == ""
    assert w.detect(R(429, text="deprecated rate")) == ""
    assert w.detect(R(500)) == ""


def test_signals():
    assert "sunset" in w.detect(R(200, {"sunset": "Tue, 1 Dec 2026"}))
    assert w.detect(R(410)) == "HTTP 410"
    assert w.detect(R(404)) == "HTTP 404"
    assert "decommission" in w.detect(R(400, text='{"message":"API decommissioned"}'))


def test_detect_never_raises():
    assert w.detect(object()) == ""


def test_ended_listing_404_is_not_a_signal():
    assert w.detect(R(404), "browse/item") == ""
    assert w.detect(R(410), "browse/item") == ""
    assert w.detect(R(404), "browse/item_summary/search") == "HTTP 404"
    assert w.detect(R(301), "browse/item") == "HTTP 301"
