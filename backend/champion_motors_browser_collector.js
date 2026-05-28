// ============================================================
// Champion Motors DOM Collector v1
// Cloudflare-proof: reads ONLY the already-rendered page DOM
// ============================================================
//
// HOW TO USE:
//   1. Go to https://www.championmotors.co.il/catalog/
//   2. Open DevTools > Sources > Snippets  (F12 → Sources tab → Snippets)
//   3. Paste this entire script as a new snippet, Save it
//
//   RUN MODE A -- auto-paginate (RECOMMENDED):
//     Navigate to a category page and run once.
//     Script collects current page, then auto-clicks "Next" every 3s.
//
//   RUN MODE B -- manual page-by-page:
//     Run snippet on each listing page manually.
//     Then click Next yourself and run again.
//
//   DOWNLOAD -- run in console when done:
//     cmDownload()
//
//   STATUS CHECK:
//     cmStatus()
//
//   CLEAR (start over):
//     cmClear()
// ============================================================

(function cmCollect() {
  const STORAGE_KEY = 'cm_parts_collected';

  let collected = {};
  try { collected = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); } catch {}

  let added = 0;

  // ---- Helper: normalise & store a part ----
  function addPart(p) {
    const key = (p.oem_number || p.sku || '').trim().replace(/\s+/g, '');
    if (!key || key.length < 3) return;
    if (collected[key]) return;   // already have it
    collected[key] = {
      sku:         (p.sku         || key).trim(),
      name:        (p.name        || '').trim().slice(0, 255),
      description: (p.description || '').trim().slice(0, 500),
      price_ils:   parseFloat(p.price) || 0,
      oem_number:  key,
      category:    (p.category    || '').trim(),
      image_url:   (p.image_url   || '').trim().slice(0, 300),
      brand:       (p.brand       || '').trim(),
      model:       (p.model       || '').trim(),
      in_stock:    !!p.in_stock,
    };
    added++;
  }

  // ---- Method 1: JSON-LD structured data ----
  document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
    try {
      const d = JSON.parse(s.textContent);
      const items = Array.isArray(d) ? d : [d];
      items.forEach(item => {
        if (item['@type'] === 'Product' || item.sku || item.mpn) {
          addPart({
            sku:        item.sku || item.mpn || '',
            name:       item.name || '',
            description:item.description || '',
            price:      item.offers?.price || item.offers?.[0]?.price || 0,
            oem_number: item.mpn || item.sku || '',
            category:   item.category || '',
            image_url:  (Array.isArray(item.image) ? item.image[0] : item.image) || '',
            in_stock:   (item.offers?.availability || '').includes('InStock'),
          });
        }
        if (item['@type'] === 'ItemList' || item.itemListElement) {
          (item.itemListElement || []).forEach(el => {
            const p = el.item || el;
            if (p.sku || p.mpn || p.name) addPart({
              sku: p.sku || p.mpn || '', name: p.name || '',
              description: p.description || '', price: p.offers?.price || 0,
              oem_number: p.mpn || p.sku || '', category: p.category || '',
              image_url: p.image || '', in_stock: false,
            });
          });
        }
      });
    } catch {}
  });

  // ---- Method 2: WooCommerce / Next.js / React globals ----
  const globals = [
    window.__NEXT_DATA__?.props?.pageProps,
    window._wca,
    window.wc_cart_fragments_params,
    window.__INITIAL_STATE__,
    window.APP_DATA,
    window.BCData,
    window.BCAPP_DATA,
    window.__BC_DATA__,
    (() => { try { return window.jsContext && JSON.parse(window.jsContext || '{}'); } catch { return null; } })(),
  ];
  globals.forEach(g => {
    if (!g) return;
    const candidates = g.products || g.items || g.results || g.searchResults?.products ||
                       g.category?.products || g.productResults || g.catalog_products || [];
    (Array.isArray(candidates) ? candidates : []).forEach(p => addPart({
      sku:        p.sku || p.product_id || p.entityId || '',
      name:       p.name || p.title || '',
      description:p.description || p.short_description || '',
      price:      p.price?.value || p.prices?.price?.value || p.regular_price || p.price || 0,
      oem_number: p.mpn || p.sku || p.reference_number || '',
      category:   p.categories?.[0]?.name || p.categories?.[0] || p.category || '',
      image_url:  p.images?.[0]?.src || p.defaultImage?.url || p.image?.url || p.thumbnail || '',
      brand:      p.brands?.[0]?.name || p.brand || p.manufacturer || '',
      model:      p.model || '',
      in_stock:   p.availability === 'available' || p.in_stock === true || p.inStock === true,
    }));
  });

  // ---- Method 3: WooCommerce product loop (common HTML structure) ----
  // Champion Motors likely uses WooCommerce or a custom Israeli platform
  const wooCandidates = [
    '.products li.product',
    '.woocommerce-loop-product',
    '.product-card',
    '.product-item',
    '[data-product-id]',
    '[data-entity-id]',
    '[itemtype*="Product"]',
    '.catalog-product',
    '.part-item',
  ];
  wooCandidates.forEach(sel => {
    document.querySelectorAll(sel).forEach(el => {
      const nameEl  = el.querySelector('.woocommerce-loop-product__title, .product-title, .card-title, h2, h3, [itemprop="name"]');
      const name    = (nameEl?.textContent || '').trim();
      const skuEl   = el.querySelector('.sku, [itemprop="sku"], [data-sku], .product-sku, .reference');
      const sku     = (skuEl?.textContent || el.dataset.productId || el.dataset.entityId || '').trim();
      const mpnEl   = el.querySelector('[itemprop="mpn"], .mpn, [data-mpn], .oem-number, .part-number');
      const oem     = (mpnEl?.textContent || mpnEl?.dataset?.mpn || sku).trim();
      const priceEl = el.querySelector('.woocommerce-Price-amount, .price, [itemprop="price"], .product-price');
      const price   = parseFloat((priceEl?.textContent || '0').replace(/[^0-9.]/g, '')) || 0;
      const imgEl   = el.querySelector('img[src], img[data-src], img[data-lazy-src]');
      const imgUrl  = imgEl?.src || imgEl?.dataset?.src || imgEl?.dataset?.lazySrc || '';
      const brandEl = el.querySelector('.brand, .manufacturer, [itemprop="brand"]');
      const brand   = (brandEl?.textContent || '').trim();
      if (name || oem || sku) addPart({ sku, name, price, oem_number: oem || sku, image_url: imgUrl, brand, in_stock: !el.classList.contains('out-of-stock') });
    });
  });

  // ---- Method 4: Single product page ----
  const h1El    = document.querySelector('h1.product_title, h1.entry-title, h1[itemprop="name"], h1');
  const skuEl   = document.querySelector('.sku, [itemprop="sku"], .product-sku, [data-sku]');
  const mpnEl   = document.querySelector('[itemprop="mpn"], .mpn, .oem-number, .part-number, .reference-number');
  const priceEl = document.querySelector('[itemprop="price"], .woocommerce-Price-amount, .price--main, .product-price');
  const imgEl   = document.querySelector('.woocommerce-product-gallery img, .product-image img, [itemprop="image"]');
  const descEl  = document.querySelector('[itemprop="description"], .woocommerce-product-details__short-description, .product-description');
  const catEl   = document.querySelector('.breadcrumb li:nth-last-child(2), .woocommerce-breadcrumb a:last-of-type');
  const brandEl = document.querySelector('[itemprop="brand"], .brand-name, .manufacturer-name');

  const h1Text = (h1El?.textContent || '').trim();
  const oem    = (mpnEl?.textContent || skuEl?.textContent || '').trim();
  if (h1Text && (oem || skuEl?.textContent?.trim())) {
    addPart({
      sku:        (skuEl?.textContent || '').trim(),
      name:       h1Text,
      description:(descEl?.textContent || '').trim().slice(0, 500),
      price:      parseFloat((priceEl?.textContent || '0').replace(/[^0-9.]/g, '')) || 0,
      oem_number: oem || (skuEl?.textContent || '').trim() || document.title.match(/\b([A-Z0-9]{6,})\b/)?.[1] || '',
      category:   (catEl?.textContent || '').trim(),
      image_url:  imgEl?.src || imgEl?.getAttribute('content') || '',
      brand:      (brandEl?.textContent || '').trim(),
      in_stock:   !!document.querySelector('.in-stock, [itemprop="availability"][href*="InStock"], .stock.in-stock'),
    });
  }

  // ---- Method 5: scan all links with OEM-like text near price ----
  // Fallback: look for anything that looks like a part number in the page
  if (added === 0) {
    const oems = new Set();
    document.querySelectorAll('td, .sku, .part-no, .reference, [class*="oem"], [class*="part-number"]').forEach(el => {
      const txt = el.textContent.trim();
      // OEM pattern: alphanumeric, 6-20 chars, contains at least one digit
      if (/^[A-Z0-9\-\.]{6,20}$/.test(txt) && /\d/.test(txt) && !oems.has(txt)) {
        oems.add(txt);
        const row = el.closest('tr, li, .item, .product');
        const nameTxt = row?.querySelector('td:first-child, .name, .title, h3, h4')?.textContent?.trim() || txt;
        const priceTxt = row?.querySelector('td:last-child, .price')?.textContent?.replace(/[^0-9.]/g, '') || '0';
        addPart({ sku: txt, name: nameTxt, price: parseFloat(priceTxt) || 0, oem_number: txt });
      }
    });
  }

  // ---- Save ----
  localStorage.setItem(STORAGE_KEY, JSON.stringify(collected));
  const total = Object.keys(collected).length;
  console.log(
    `%c[CM Collector] +${added} new | ${total} total | ${location.pathname}`,
    'color:#22c55e;font-weight:bold;font-size:14px'
  );

  // ---- Auto-paginate ----
  const nextBtn = document.querySelector(
    'a[rel="next"], .next.page-numbers, .pagination-item--next a, a.next, li.next a, [data-page="next"], .woocommerce-pagination a.next'
  );
  if (nextBtn?.href) {
    console.log(`%c[CM Collector] Next → ${nextBtn.href} (3s)`, 'color:#60a5fa');
    setTimeout(() => { window.location.href = nextBtn.href; }, 3000);
  } else {
    console.log('%c[CM Collector] End of pages. Run cmDownload() to export.', 'color:#f59e0b;font-weight:bold');
  }

  // ---- Export / utility functions ----
  window.cmDownload = function() {
    const arr = Object.values(collected);
    if (arr.length === 0) { console.warn('No parts collected yet. Browse the catalog first.'); return; }
    const out = {
      source: 'championmotors.co.il',
      collected_at: new Date().toISOString(),
      total_parts: arr.length,
      parts: arr,
    };
    const blob = new Blob([JSON.stringify(out, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `champion_motors_parts_${arr.length}.json`;
    a.click();
    console.log(`%cDownloaded ${arr.length} parts → upload at http://94.130.150.23:8080`, 'color:#22c55e;font-size:13px');
  };

  window.cmStatus = function() {
    const data = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    const arr = Object.values(data);
    const brands = {};
    arr.forEach(p => { const b = p.brand || 'Unknown'; brands[b] = (brands[b] || 0) + 1; });
    console.log(`%cCM Collector: ${arr.length} parts collected`, 'color:#22c55e;font-weight:bold;font-size:14px');
    console.table(brands);
  };

  window.cmClear = function() {
    localStorage.removeItem(STORAGE_KEY);
    collected = {};
    console.log('%cCleared all collected CM parts.', 'color:#ef4444');
  };

  return `+${added} this page | ${total} total`;
})();
