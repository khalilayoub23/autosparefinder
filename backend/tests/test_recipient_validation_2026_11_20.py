"""2026-11-20: placeholder / malformed supplier recipients (info@yourwebsite.com sent 2026-11-18; management@autoworks.co.i x39)."""
import pytest
import supplier_qualification as q


@pytest.mark.parametrize("addr", [
    "info@yourwebsite.com", "management@autoworks.co.i", "name@example.com", "a@example.co.uk", "x@sub.example.org",
    "user@sentry-next.wixpress.com", "abc@sentry.io", "logo@2x.png", "a@b.c", "", None, "no-at-sign", "a b@c.com",
    "info@domain.com", "you@yourcompany.com",
])
def test_rejected(addr):
    assert q.valid_email(addr) is False


@pytest.mark.parametrize("addr", ["sales@exoracing.co.uk", "contact@vinyasaautomotive.com", "info@europarts360.com", "x.y+z@a-b.co.il"])
def test_accepted(addr):
    assert q.valid_email(addr) is True


def test_both_outreach_gates_use_valid_email():
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "supplier_outreach.py").read_text(encoding="utf-8")
    assert len(re.findall(r"_q\.valid_email\(", src)) >= 2          # plan() and send_outreach()
