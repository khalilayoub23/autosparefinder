/**
 * Land Rover OEM Parts Scraper — runs in browser DevTools console
 * Site: https://landrover.oempartsonline.com
 *
 * HOW TO USE:
 * 1. Open https://landrover.oempartsonline.com/search?search_str=fuse in Chrome
 * 2. Open DevTools (F12) → Console tab
 * 3. Paste this entire script and press Enter
 * 4. Wait 30-90 minutes (it will log progress)
 * 5. It auto-downloads land_rover_parts.json when done
 */

(async function() {
  const DELAY_MS     = 1200;
  const MAX_PER_PAGE = 50;
  const seen         = new Map();

  async function searchPage(q, page = 1) {
    const url = `/ajax/search?search_str=${encodeURIComponent(q)}&page=${page}&per_page=${MAX_PER_PAGE}`;
    try {
      const r = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' } });
      if (!r.ok) return null;
      return await r.json();
    } catch(e) { return null; }
  }

  function normalizePart(raw) {
    return {
      sku:         raw.sku        || raw.part_number || '',
      name:        raw.name       || raw.title       || '',
      description: raw.description|| '',
      price:       parseFloat(raw.price || raw.retail_price || 0),
      oem_number:  raw.sku        || raw.oem_number  || raw.part_number || '',
      category:    raw.category   || raw.categories?.[0]?.name || '',
      image:       raw.image_url  || raw.thumbnail   || '',
      in_stock:    raw.in_stock   ?? true,
    };
  }

  async function enumerateBySearch(q) {
    const first = await searchPage(q, 1);
    if (!first) return 0;

    const data    = first.data   || first.results   || first;
    const items   = Array.isArray(data) ? data
                  : (data.items  || data.products || data.parts || []);
    const total   = data.total   || data.total_count || items.length;
    const perPage = data.per_page|| MAX_PER_PAGE;
    const pages   = Math.ceil(total / perPage);

    let added = 0;
    const processPage = (pageItems) => {
      for (const raw of pageItems) {
        const p = normalizePart(raw);
        if (p.sku && !seen.has(p.sku)) { seen.set(p.sku, p); added++; }
      }
    };

    processPage(items);

    for (let pg = 2; pg <= Math.min(pages, 20); pg++) {
      await new Promise(r => setTimeout(r, DELAY_MS));
      const more = await searchPage(q, pg);
      if (!more) break;
      const mData  = more.data || more.results || more;
      const mItems = Array.isArray(mData) ? mData : (mData.items || mData.products || []);
      if (!mItems.length) break;
      processPage(mItems);
    }
    return added;
  }

  const CHARS = 'abcdefghijklmnopqrstuvwxyz0123456789';
  const queries2 = [];
  for (const a of CHARS) for (const b of CHARS) queries2.push(a + b);

  const KEYWORDS = [
    'LR0','LR1','LR2','LR3','LR4','LR5','LR6','LR7','LR8','LR9',
    'ANR','AMR','ESR','FTC','NRC','PRC','RTC','STC','WLD','YWB',
    'filter','gasket','seal','bearing','bush','bracket','bolt','sensor',
    'pump','valve','hose','pipe','clip','ring','pad','disc','lamp',
    'switch','relay','fuse','belt','pulley','motor','cable','cover',
  ];

  let phase1Done = 0;
  const TOTAL = queries2.length;
  const cappedPrefixes = [];

  console.log(`[LR Scraper] Phase 1: ${TOTAL} 2-char searches starting...`);

  for (let i = 0; i < queries2.length; i++) {
    const q = queries2[i];
    await new Promise(r => setTimeout(r, DELAY_MS));
    const first = await searchPage(q, 1);
    if (!first) continue;

    const data  = first.data || first.results || first;
    const total = data?.total || data?.total_count || 0;
    const items = Array.isArray(data) ? data : (data?.items || data?.products || []);

    let added = 0;
    for (const raw of items) {
      const p = normalizePart(raw);
      if (p.sku && !seen.has(p.sku)) { seen.set(p.sku, p); added++; }
    }

    if (total >= MAX_PER_PAGE || items.length >= MAX_PER_PAGE) {
      cappedPrefixes.push(q);
    }

    phase1Done++;
    if (phase1Done % 100 === 0) {
      console.log(`[LR Scraper] Phase 1: ${phase1Done}/${TOTAL} | unique=${seen.size} | capped=${cappedPrefixes.length}`);
    }
  }

  console.log(`[LR Scraper] Phase 1 done. ${seen.size} unique parts. ${cappedPrefixes.length} capped prefixes.`);

  if (cappedPrefixes.length > 0) {
    console.log(`[LR Scraper] Phase 2: expanding ${cappedPrefixes.length} prefixes \u00d7 36 = ${cappedPrefixes.length * 36} searches`);
    let p2done = 0;
    for (const prefix of cappedPrefixes) {
      for (const c of CHARS) {
        await new Promise(r => setTimeout(r, DELAY_MS));
        await enumerateBySearch(prefix + c);
        p2done++;
      }
      if (p2done % 50 === 0) console.log(`[LR Scraper] Phase 2: ${p2done}/${cappedPrefixes.length * 36} | unique=${seen.size}`);
    }
  }

  console.log(`[LR Scraper] Phase 3: ${KEYWORDS.length} keyword searches`);
  for (const kw of KEYWORDS) {
    await new Promise(r => setTimeout(r, DELAY_MS));
    await enumerateBySearch(kw);
  }

  const parts = Array.from(seen.values());
  console.log(`\n[LR Scraper] COMPLETE \u2014 ${parts.length} unique parts found`);

  const output = {
    manufacturer: 'Land Rover',
    scraped_at:   new Date().toISOString(),
    source:       'landrover.oempartsonline.com',
    total:        parts.length,
    parts:        parts,
  };

  const blob = new Blob([JSON.stringify(output, null, 2)], { type: 'application/json' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = 'land_rover_parts.json';
  a.click();
  URL.revokeObjectURL(url);
  console.log('[LR Scraper] Downloaded land_rover_parts.json');
})();
