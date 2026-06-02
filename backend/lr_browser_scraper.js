// ============================================================
// Land Rover DOM Collector v3 -- NO fetch() calls at all
// Cloudflare-proof: reads ONLY the already-rendered page DOM
// ============================================================
//
// HOW TO USE:
//   1. Go to landrover.oempartsonline.com  (any page)
//   2. Open DevTools > Sources > Snippets
//   3. Paste this entire script as a snippet
//
//   RUN MODE A -- collect current page (run on each listing page):
//     Run snippet normally.  Products saved to localStorage.
//     Then click "Next page" on the site and run again.
//
//   RUN MODE B -- auto-paginate (run ONCE on a category/search page):
//     The script will click the "Next" button and re-inject itself.
//
//   DOWNLOAD -- run this in console when done browsing:
//     lrDownload()
// ============================================================

(function lrCollect() {
  const STORAGE_KEY = 'lr_parts_collected';

  // ---- Load existing collection from localStorage ----
  let collected = {};
  try { collected = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); } catch {}

  let added = 0;

  // ---- Helper: add a part record ----
  function addPart(p) {
    const key = (p.oem_number || p.sku || p.name || '').trim();
    if (!key || collected[key]) return;
    collected[key] = {
      sku:         (p.sku         || '').trim(),
      name:        (p.name        || '').trim(),
      description: (p.description || '').trim().slice(0,500),
      price:       parseFloat(p.price) || 0,
      oem_number:  key,
      category:    (p.category    || '').trim(),
      image_url:   (p.image_url   || '').trim().slice(0,300),
      in_stock:    !!p.in_stock,
    };
    added++;
  }

  // ---- Method 1: JSON-LD structured data (most reliable) ----
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
            image_url:  item.image || item.image?.[0] || '',
            in_stock:   (item.offers?.availability || '').includes('InStock'),
          });
        }
        // ItemList
        if (item.itemListElement) {
          item.itemListElement.forEach(el => {
            const p = el.item || el;
            if (p.sku || p.mpn || p.name) addPart({
              sku: p.sku||p.mpn||'', name: p.name||'',
              description: p.description||'', price: p.offers?.price||0,
              oem_number: p.mpn||p.sku||'', category: p.category||'',
              image_url: p.image||'', in_stock: false,
            });
          });
        }
      });
    } catch {}
  });

  // ---- Method 2: BigCommerce / Next.js window globals ----
  const globals = [
    window.__NEXT_DATA__?.props?.pageProps,
    window.__INITIAL_STATE__,
    window.APP_DATA,
    window.BCData,
    window.BCAPP_DATA,
    window.__BC_DATA__,
    window.jsContext && JSON.parse(window.jsContext || '{}'),
  ];
  globals.forEach(g => {
    if (!g) return;
    const candidates = g.products || g.items || g.results || g.searchResults?.products ||
                       g.category?.products || g.productResults || [];
    (Array.isArray(candidates) ? candidates : []).forEach(p => addPart({
      sku:        p.sku || p.entityId || '',
      name:       p.name || '',
      description:p.description || '',
      price:      p.price?.value || p.prices?.price?.value || p.salePrice || p.price || 0,
      oem_number: p.mpn || p.sku || '',
      category:   p.categories?.[0] || p.category || '',
      image_url:  p.defaultImage?.url || p.mainImage?.url || p.image?.url || '',
      in_stock:   p.availability === 'available' || p.inStock === true,
    }));
  });

  // ---- Method 3: DOM product cards ----
  const cardSelectors = [
    '[data-product-id]', '[data-entity-id]', '[data-item-id]',
    '.productCard', '.product-card', '.card--product',
    '[itemtype*="Product"]', 'article.product',
    '.product-listing li', '.products-list li',
  ];
  cardSelectors.forEach(sel => {
    document.querySelectorAll(sel).forEach(el => {
      const name  = (el.querySelector('.card-title,.product-name,.productCard-title,[itemprop="name"],h3,h4')?.textContent || '').trim();
      const sku   = el.dataset.productId || el.dataset.entityId || el.dataset.itemId ||
                    el.querySelector('[itemprop="sku"],[data-sku]')?.textContent?.trim() || '';
      const price = parseFloat(
        el.querySelector('[data-product-price],[itemprop="price"],.price,.productCard-price')
          ?.textContent?.replace(/[^0-9.]/g,'') || '0'
      ) || 0;
      const img   = el.querySelector('img[src],img[data-src]');
      const imgUrl= img?.src || img?.dataset?.src || '';
      const oem   = el.querySelector('[itemprop="mpn"],[data-mpn]')?.textContent?.trim() || sku;
      if (name || sku) addPart({ sku, name, description:'', price, oem_number: oem, category:'', image_url: imgUrl, in_stock: true });
    });
  });

  // ---- Method 4: current single product page ----
  const h1 = document.querySelector('h1.productView-title, h1.product-title, h1[itemprop="name"], h1');
  const skuEl  = document.querySelector('[itemprop="sku"], .sku-value, .product-sku, [data-sku]');
  const priceEl= document.querySelector('[itemprop="price"], .price--main, .productView-price .price');
  const imgEl  = document.querySelector('.productView-image img, [itemprop="image"]');
  const descEl = document.querySelector('[itemprop="description"], .productView-description, .product-description');
  if (h1?.textContent?.trim()) {
    addPart({
      sku:        skuEl?.textContent?.trim() || skuEl?.content || '',
      name:       h1.textContent.trim(),
      description:descEl?.textContent?.trim().slice(0,500) || '',
      price:      parseFloat(priceEl?.textContent?.replace(/[^0-9.]/g,'') || '0') || 0,
      oem_number: skuEl?.textContent?.trim() || document.title.match(/\(([A-Z0-9]+)\)/)?.[1] || '',
      category:   document.querySelector('.breadcrumb li:nth-last-child(2)')?.textContent?.trim() || '',
      image_url:  imgEl?.src || imgEl?.getAttribute('content') || '',
      in_stock:   !!document.querySelector('[itemprop="availability"][href*="InStock"], .stock-in'),
    });
  }

  // ---- Save back to localStorage ----
  localStorage.setItem(STORAGE_KEY, JSON.stringify(collected));
  const total = Object.keys(collected).length;
  console.log(`%c[LR Collector] +${added} new parts | ${total} total saved | Page: ${location.pathname}`,
    'color:#22c55e;font-weight:bold;font-size:14px');

  // ---- Auto-paginate: click Next if exists ----
  const nextBtn = document.querySelector(
    'a[rel="next"], .pagination-item--next a, [data-page="next"], a.next, li.next a'
  );
  if (nextBtn && nextBtn.href) {
    console.log(`%c[LR Collector] Next page: ${nextBtn.href} -- navigating in 2s...`,
      'color:#60a5fa');
    setTimeout(() => { window.location.href = nextBtn.href; }, 2000);
  } else {
    console.log('%c[LR Collector] No next page found. Run lrDownload() to export.',
      'color:#f59e0b');
  }

  // ---- Expose download function globally ----
  window.lrDownload = function() {
    const arr = Object.values(collected);
    const blob = new Blob(
      [JSON.stringify({ manufacturer:'Land Rover', total:arr.length, parts: arr }, null, 2)],
      { type:'application/json' }
    );
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'land_rover_parts.json';
    a.click();
    console.log(`Downloaded ${arr.length} parts. Upload at http://94.130.150.23:8080`);
  };

  window.lrClear = function() {
    localStorage.removeItem(STORAGE_KEY);
    console.log('Cleared all collected LR parts.');
  };

  window.lrStatus = function() {
    const n = Object.keys(JSON.parse(localStorage.getItem(STORAGE_KEY)||'{}')).length;
    console.log(`Collected: ${n} parts`);
  };

  return `+${added} parts this page | ${total} total`;
})();
