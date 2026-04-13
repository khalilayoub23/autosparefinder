import asyncio
from playwright.async_api import async_playwright

SOURCES = [
    ("autodoc.eu", "https://www.autodoc.eu/search?q=NGK+BKR6E"),
    ("partsouq.com", "https://partsouq.com/en/search/all?q=BKR6E"),
    ("7zap.com", "https://www.7zap.com/en/catalog/cars/"),
    ("epc-data toyota", "https://toyota.epc-data.com/"),
    ("epc-data honda", "https://honda.epc-data.com/"),
    ("epc-data hyundai", "https://hyundai.epc-data.com/"),
    ("epc-data subaru", "https://subaru.epc-data.com/"),
    ("realoem.com", "https://www.realoem.com/bmw/enUS/select"),
    ("motorstore.co.il", "https://www.motorstore.co.il/search?q=NGK"),
    ("meyle.com", "https://www.meyle.com/en/search/?q=oil+filter"),
    ("bilstein.com", "https://bilstein.com/en/search/?q=shock"),
    ("mann-filter.com", "https://www.mann-filter.com/en/search.html?q=W940"),
    ("febi.com", "https://www.febi.com/en/search/?q=oil+filter"),
    ("ngk.com", "https://www.ngk.com/en/search/?q=BKR6E"),
    ("bosch aftermarket", "https://www.boschautoparts.com/en/auto-parts"),
    ("gates.com", "https://www.gates.com/us/en/search.html?q=timing+belt"),
    ("skf.com", "https://www.skf.com/en/search?q=wheel+bearing"),
    ("brembo.com", "https://www.brembo.com/en/search?q=brake+disc"),
    ("mahle.com", "https://www.mahle-aftermarket.com/en/search/?q=filter"),
    ("hella.com", "https://www.hella.com/en/search?q=oil+filter"),
    ("denso.com", "https://www.denso.com/global/en/products-and-services/automotive-parts/"),
    ("valeo.com", "https://www.valeo.com/en/search/?q=filter"),
    ("sachs.de", "https://www.sachs.de/en/search/?q=clutch"),
    ("kyb.com", "https://www.kyb.com/en/search/?q=shock"),
]

BLOCK_MARKERS = [
    "captcha",
    "just a moment",
    "verify you are human",
    "access denied",
    "enable javascript and cookies",
    "checking your browser",
]


async def test(browser, name, url):
    try:
        page = await browser.new_page()
        await page.goto(url, timeout=25000)
        await page.wait_for_load_state("networkidle", timeout=12000)
        content = await page.content()
        blocked = any(m in content.lower() for m in BLOCK_MARKERS)
        has_parts = any(
            m in content.lower()
            for m in [
                "part number",
                "price",
                "sku",
                "article",
                "catalog",
                "\u05de\u05d7\u05d9\u05e8",
                "\u05de\u05e7\u05d8",
            ]
        )
        status = "OK" if (not blocked and has_parts) else "FAIL"
        print(f"[{status}] {name}: loaded={len(content)} blocked={blocked} has_parts={has_parts}")
        await page.close()
    except Exception as e:
        print(f"[FAIL] {name}: ERROR - {str(e)[:80]}")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        for name, url in SOURCES:
            await test(browser, name, url)
        await browser.close()


asyncio.run(main())