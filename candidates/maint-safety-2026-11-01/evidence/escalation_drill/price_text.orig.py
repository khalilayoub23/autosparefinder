"""parse_price(text) -> float: customer-typed prices in Israeli/European/US notation."""
import re


def parse_price(text: str) -> float:
    s = re.sub(r"[^\d.,\s]", "", text).strip()
    s = s.replace(",", "").replace(" ", "")
    return float(s)
