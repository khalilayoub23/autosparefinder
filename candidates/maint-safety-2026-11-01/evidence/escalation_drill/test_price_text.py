from price_text import parse_price

CASES = {"1,234.50": 1234.5, "1.234,50": 1234.5, "1,234": 1234.0, "12.5": 12.5, "12,5": 12.5, "1 234,50": 1234.5, "₪99": 99.0, "99": 99.0, "0,125": 0.125, "0.125": 0.125, "1.234.567": 1234567.0, "1,234,567.89": 1234567.89,
         # regression: a lone separator + 3 digits is thousands ONLY after a non-zero 1-3 digit lead
         ",125": 0.125, ".125": 0.125, "0.050": 0.05, "10,125": 10125.0, "999.999": 999999.0, "1234,567": 1234.567, "₪ 0,5": 0.5}

def main():
    bad = []
    for text, want in CASES.items():
        try:
            got = parse_price(text)
        except Exception as e:  # noqa: BLE001
            got = f"{type(e).__name__}: {e}"
        if got != want:
            bad.append((text, want, got))
    for b in bad:
        print("FAIL", b)
    print(f"{len(CASES) - len(bad)}/{len(CASES)} passed")
    raise SystemExit(1 if bad else 0)

if __name__ == "__main__":
    main()
