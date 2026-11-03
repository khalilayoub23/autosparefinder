"""parse_price(text) -> float: customer-typed prices in Israeli/European/US notation.

Rule (separators are '.' and ','; spaces are always thousands grouping):
  1. Both '.' and ',' present -> the LAST one is the decimal separator, the other is grouping.
  2. One kind, repeated (1.234.567)  -> grouping.
  3. One kind, once:
       grouping  only if exactly 3 digits follow AND the integer part is a valid leading
                 thousands group (1-3 digits, not starting with '0')   -> "1,234" = 1234
       decimal   otherwise                                               -> "12,5", "0,125", ",125"
Documented ambiguity: "1,234" / "1.234" (lone separator, 3 trailing digits, non-zero lead) is
genuinely ambiguous between 1234 and 1.234; it is resolved as THOUSANDS (prices with 3 decimals
are not a real currency format, ILS/EUR/USD use 2). A leading-zero group can never be a thousands
group, so "0,125" is unambiguous: 0.125. Malformed grouping (e.g. "1,23,4") is not validated.
"""
import re


def parse_price(text: str) -> float:
    s = re.sub(r"[^\d.,]", "", text)          # drops currency signs and all whitespace
    seps = re.findall(r"[.,]", s)
    if "." in s and "," in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        grp = "." if dec == "," else ","
        s = s.replace(grp, "").replace(dec, ".")
    elif len(seps) > 1:
        s = s.replace(seps[0], "")
    elif len(seps) == 1:
        intpart, frac = s.split(seps[0])
        if len(frac) == 3 and re.fullmatch(r"[1-9]\d{0,2}", intpart):
            s = intpart + frac
        else:
            s = intpart + "." + frac
    return float(s)
