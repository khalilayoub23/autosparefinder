/**
 * car_parts_ie_crawler_universal.js
 * Universal in-browser crawler for car-parts.ie — ANY make.
 *
 * HOW TO USE:
 *   1. Open any car-parts.ie page in Chrome (must have CF clearance)
 *   2. Open DevTools console (F12)
 *   3. Paste this entire script and press Enter
 *   4. Wait — progress is logged to console
 *   5. When done, run:  copy(JSON.stringify(window._cpieData))
 *      then paste into a file on the server
 *
 * CONFIG (edit before pasting):
 */

window._cpieConfig = {
  // Brands to crawl — remove brands you don't want, or add more
  // Format: { maker_id, maker_name, maker_slug }
  // maker_id is from the select[name="maker_id"] on any car-parts.ie page
  brands: [
    { maker_id: 121, maker_name: "Volkswagen", maker_slug: "volkswagen" },
    { maker_id: 36,  maker_name: "Ford",       maker_slug: "ford"       },
    { maker_id: 111, maker_name: "Toyota",     maker_slug: "toyota"     },
    { maker_id: 16,  maker_name: "BMW",        maker_slug: "bmw"        },
    { maker_id: 183, maker_name: "Hyundai",    maker_slug: "hyundai"    },
    { maker_id: 184, maker_name: "Kia",        maker_slug: "kia"        },
    { maker_id: 80,  maker_name: "Nissan",     maker_slug: "nissan"     },
    { maker_id: 45,  maker_name: "Honda",      maker_slug: "honda"      },
    { maker_id: 69,  maker_name: "Mazda",      maker_slug: "mazda"      },
    { maker_id: 104, maker_name: "Subaru",     maker_slug: "subaru"     },
    { maker_id: 75,  maker_name: "Mitsubishi", maker_slug: "mitsubishi" },
    { maker_id: 93,  maker_name: "Renault",    maker_slug: "renault"    },
    { maker_id: 74,  maker_name: "Mercedes-Benz", maker_slug: "mercedes-benz" },
    { maker_id: 21,  maker_name: "Citroen",    maker_slug: "citroen"    },
    { maker_id: 88,  maker_name: "Peugeot",    maker_slug: "peugeot"    },
  ],
  // Max pages per category (safety cap)
  maxPagesPerCategory: 10,
  // Delay between pages (ms) — be polite
  pageDelay: 200,
  // Models to sample per brand (0 = all, 1 = first only, 3 = first 3)
  // Use 1 for a quick test; 0 for full harvest
  modelsPerBrand: 1,
};

// ── State ────────────────────────────────────────────────────────────────────
window._cpieData = {};       // { brand_slug: { meta, parts[] } }
window._cpieProgress = { currentBrand: null, done: 0, total: window._cpieConfig.brands.length, errors: 0 };
window._cpieDone = false;

// ── Helpers ──────────────────────────────────────────────────────────────────
function parseParts(html, catSlug, carMeta) {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const items = doc.querySelectorAll('.item');
  const parts = [];
  items.forEach(item => {
    const titleLink = item.querySelector('.item_title a');
    if (!titleLink) return;
    const artEl = item.querySelector('.item_artikle');
    const artRaw = artEl ? artEl.textContent.replace(/Art\.?\s*[№No]\.?:?\s*/i, '').trim() : '';
    const priceDiv = item.querySelector('[data-price]');
    parts.push({
      name:         titleLink.textContent.trim(),
      product_url:  titleLink.href,
      inferred_sku: artRaw,
      price_eur:    priceDiv ? parseFloat(priceDiv.dataset.price) : null,
      product_id:   priceDiv?.dataset?.productId || null,
      category:     catSlug,
    });
  });
  const nextPage = Math.max(0, ...[...doc.querySelectorAll('a[href*="?page="]')]
    .map(a => parseInt(a.href.match(/page=(\d+)/)?.[1])).filter(Boolean));
  return { parts, maxPage: nextPage };
}

async function fetchPage(url) {
  const r = await fetch(url, { credentials: 'include' });
  return r.text();
}

async function scrapeCar(carMeta) {
  // carMeta: { maker_slug, maker_id, maker_name, model_slug, car_alias, car_id, model_text, engine_text }
  const base = `https://www.car-parts.ie/car-parts/${carMeta.maker_slug}/${carMeta.model_slug}/${carMeta.car_alias}`;
  console.log(`  [cpie] Discovering categories for car_id=${carMeta.car_id} ...`);

  // Load the car's first page to discover categories
  const html0 = await fetchPage(`${base}/undefined/${carMeta.car_id}`);
  const doc0 = new DOMParser().parseFromString(html0, 'text/html');

  // Expand all category accordions
  doc0.querySelectorAll('.categories-list__cat-toggle').forEach(btn => btn.click?.());
  await new Promise(r => setTimeout(r, 500));

  // Re-fetch or use current DOM links
  const catLinks = [...doc0.querySelectorAll('a[href*="/car-parts/"]')]
    .map(a => {
      const m = a.href.match(/\/car-parts\/[^/]+\/[^/]+\/[^/]+\/([^/]+)\/(\d+)/);
      return m ? { catSlug: m[1], carId: parseInt(m[2]) } : null;
    })
    .filter(l => l && l.carId === carMeta.car_id);

  const cats = [...new Map(catLinks.map(l => [l.catSlug, l])).values()];
  console.log(`  [cpie]   ${cats.length} categories found`);

  const parts = [];
  for (const cat of cats) {
    let page = 1;
    while (page <= window._cpieConfig.maxPagesPerCategory) {
      const url = `${base}/${cat.catSlug}/${carMeta.car_id}?page=${page}`;
      const html = await fetchPage(url);
      const { parts: pageParts, maxPage } = parseParts(html, cat.catSlug, carMeta);
      parts.push(...pageParts);
      if (page >= maxPage || pageParts.length === 0) break;
      page++;
      await new Promise(r => setTimeout(r, window._cpieConfig.pageDelay));
    }
  }
  return parts;
}

async function discoverCar(makerSel, modelSel, carSel, brandCfg) {
  // Select the maker and wait for model select to repopulate
  makerSel.value = String(brandCfg.maker_id);
  makerSel.dispatchEvent(new Event('change', { bubbles: true }));
  await new Promise(r => setTimeout(r, 1500));

  const modelOptions = [...modelSel.options].filter(o => o.value && o.value !== '0');
  if (!modelOptions.length) {
    console.warn(`  [cpie] No models found for ${brandCfg.maker_name}`);
    return null;
  }

  const limit = window._cpieConfig.modelsPerBrand || modelOptions.length;
  const selectedModels = modelOptions.slice(0, Math.max(1, limit));
  const results = [];

  for (const modelOpt of selectedModels) {
    modelSel.value = modelOpt.value;
    modelSel.dispatchEvent(new Event('change', { bubbles: true }));
    await new Promise(r => setTimeout(r, 1000));

    const carOptions = [...carSel.options].filter(o => o.value && o.value !== '0');
    if (!carOptions.length) continue;

    // Submit form to navigate to this car's parts page
    const carOpt = carOptions[0];
    carSel.value = carOpt.value;
    carSel.dispatchEvent(new Event('change', { bubbles: true }));
    await new Promise(r => setTimeout(r, 500));

    // Navigate programmatically to the car page to get JsVars
    const formAction = new URL(document.getElementById('top-select')?.action || location.href);
    formAction.searchParams.set('maker_id', brandCfg.maker_id);
    formAction.searchParams.set('model_id', modelOpt.value);
    formAction.searchParams.set('car_id', carOpt.value);

    // Fetch the page to extract JsVars without navigating away
    const html = await fetchPage(formAction.toString());
    const doc = new DOMParser().parseFromString(html, 'text/html');

    // Extract route params from embedded script
    let routeParams = null;
    for (const script of doc.querySelectorAll('script:not([src])')) {
      const m = script.textContent.match(/JsVars\s*=\s*({[^;]+})/);
      if (m) {
        try {
          const jsVars = eval('(' + m[1] + ')');
          routeParams = jsVars?.routeParams || jsVars;
        } catch (e) {}
        break;
      }
    }

    if (!routeParams?.car_id) {
      // Try URL-based extraction from any car-parts.ie link
      const sampleLink = doc.querySelector(`a[href*="/${carOpt.value}"]`);
      if (sampleLink) {
        const urlM = sampleLink.href.match(/\/car-parts\/([^/]+)\/([^/]+)\/([^/]+)\/[^/]+\/(\d+)/);
        if (urlM) {
          routeParams = {
            maker: urlM[1], model: urlM[2], car_alias: urlM[3], car_id: parseInt(urlM[4])
          };
        }
      }
    }

    if (!routeParams?.car_id) {
      console.warn(`  [cpie] Could not extract routeParams for model ${modelOpt.text}`);
      continue;
    }

    results.push({
      maker_slug:   routeParams.maker || brandCfg.maker_slug,
      maker_id:     brandCfg.maker_id,
      maker_name:   brandCfg.maker_name,
      model_slug:   routeParams.model,
      car_alias:    routeParams.car_alias,
      car_id:       routeParams.car_id,
      model_text:   modelOpt.text,
      engine_text:  carOpt.text,
    });
  }

  return results;
}

// ── Main loop ─────────────────────────────────────────────────────────────────
(async () => {
  const makerSel = document.querySelector('select[name="maker_id"]');
  const modelSel = document.querySelector('select[name="model_id"]');
  const carSel   = document.querySelector('select[name="car_id"]');

  if (!makerSel || !modelSel || !carSel) {
    console.error('[cpie] Car selector form not found. Open a car-parts.ie parts page first.');
    return;
  }

  const { brands } = window._cpieConfig;
  window._cpieProgress.total = brands.length;
  console.log(`[cpie] Starting universal crawler — ${brands.length} brands`);

  for (const brandCfg of brands) {
    window._cpieProgress.currentBrand = brandCfg.maker_name;
    console.log(`\n[cpie] ===== ${brandCfg.maker_name} (maker_id=${brandCfg.maker_id}) =====`);

    try {
      const carMetas = await discoverCar(makerSel, modelSel, carSel, brandCfg);
      if (!carMetas || !carMetas.length) {
        window._cpieProgress.errors++;
        continue;
      }

      const allParts = [];
      for (const carMeta of carMetas) {
        console.log(`  [cpie] Scraping ${carMeta.model_text} / ${carMeta.engine_text}`);
        const parts = await scrapeCar(carMeta);
        allParts.push(...parts);
        console.log(`  [cpie]   ${parts.length} parts (total: ${allParts.length})`);
      }

      window._cpieData[brandCfg.maker_slug] = {
        source:       'car-parts.ie',
        maker:        brandCfg.maker_slug,
        maker_id:     brandCfg.maker_id,
        manufacturer: brandCfg.maker_name,
        scraped_at:   new Date().toISOString(),
        car_metas:    carMetas,
        parts:        allParts,
      };

      // Auto-download JSON for this brand
      const blob = new Blob([JSON.stringify(window._cpieData[brandCfg.maker_slug], null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${brandCfg.maker_slug}_cpie.json`;
      a.click();

      console.log(`[cpie] ${brandCfg.maker_name}: ${allParts.length} parts — downloaded ${brandCfg.maker_slug}_cpie.json`);
    } catch (err) {
      console.error(`[cpie] Error on ${brandCfg.maker_name}:`, err);
      window._cpieProgress.errors++;
    }

    window._cpieProgress.done++;
    await new Promise(r => setTimeout(r, 1000));
  }

  window._cpieDone = true;
  console.log(`\n[cpie] ===== DONE ===== ${window._cpieProgress.done}/${brands.length} brands`);
  console.log('[cpie] All brand JSON files have been downloaded.');
  console.log('[cpie] To import, for each file run inside the Docker container:');
  console.log('  docker exec autospare_backend python3 /app/car_parts_ie_import_generic.py \\');
  console.log('    --brand volkswagen --file /path/to/volkswagen_cpie.json');
})();
