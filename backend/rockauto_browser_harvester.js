/* =====================================================================
 * RockAuto browser harvester  (run in the browser console on a RockAuto
 * page that shows part listings, e.g. after browsing a vehicle's parts or
 * searching a part number).  RockAuto blocks server-side scraping, so this
 * runs in YOUR real browser (valid cookies) and relays {oem, price_usd,
 * brand, name} to our collect endpoint, which fills prices for matching
 * unpriced parts (matched by normalized OEM).
 *
 * USAGE:
 *   1. Open a RockAuto page with visible part listings + prices.
 *   2. Paste this whole script in the console.
 *   3. It runs in DRY-RUN first: it prints what it found WITHOUT sending.
 *      Check the table looks right (part numbers + $ prices).
 *   4. To actually send:  RA_HARVEST.send()
 *   5. Move to the next page and repeat (or call RA_HARVEST.run() again).
 * ===================================================================== */
(function () {
  const BASE  = 'https://autosparefinder.co.il/api/v1/system';
  const RELAY = BASE + '/collect';
  const FEED  = BASE + '/unpriced-oems';
  const CHUNK = 200;
  // The collect/feed endpoints require the X-Collect-Secret header (COLLECT_SECRET
  // env). Paste it ONCE per console session:  RA_HARVEST.auth('<your-secret>')
  // It is NOT stored in this file — the owner supplies it at runtime.
  let SECRET = '';
  // Relay + feed are called CROSS-ORIGIN from a supplier page (rockauto.com) to
  // our backend. A custom header (X-Collect-Secret) would force a CORS preflight
  // that our global CORSMiddleware rejects (400). So we use "simple requests":
  // POST with Content-Type text/plain and the secret in the BODY — no preflight.
  // The backend reads the secret from the body and returns ACAO:* so we can read
  // the response. credentials:'omit' keeps ACAO:* valid.
  const postJSON = (url, obj) => fetch(url, {
    method: 'POST', credentials: 'omit',
    headers: { 'Content-Type': 'text/plain' },
    body: JSON.stringify(obj),
  });

  // Parse a RockAuto part-search page (document `doc`, searched `oem`) into
  // EVERY brand option: {oem, price_usd, brand, partnum, name}. We tag every
  // option with the OEM we searched (our catalog key), not RockAuto's brand
  // part number. RockAuto schema: dprice[N][v] = per-each price, vew_partnumber[N]
  // = brand part#, .listing-final-manufacturer = brand, .listing-text-row = cat.
  function parseDoc(doc, oem) {
    const out = [];
    const seen = new Set();
    doc.querySelectorAll('[id^="dprice["]').forEach((pn) => {
      const m = (pn.textContent || '').match(/\$\s?(\d{1,4}\.\d{2})/);
      if (!m) return;
      const price = parseFloat(m[1]);
      if (!(price > 0)) return;                         // skip $0.00 core rows
      const idx = (pn.id.match(/dprice\[(\d+)\]/) || [])[1];
      const cont = doc.getElementById(`listingcontainer[${idx}]`);
      const brand = cont?.querySelector('.listing-final-manufacturer')?.textContent?.trim() || '';
      const partnum = doc.getElementById(`vew_partnumber[${idx}]`)?.textContent?.trim() || '';
      const name = cont?.querySelector('.listing-text-row')?.textContent?.replace(/Category:\s*/i, '').trim() || '';
      const key = partnum + '|' + price;
      if (seen.has(key)) return;                         // RockAuto lists each twice
      seen.add(key);
      out.push({ oem, price_usd: price, brand, partnum, name });
    });
    return out;
  }

  // Extract from the CURRENT page (single OEM already loaded).
  function extract() {
    const oem = new URLSearchParams(location.search).get('partnum') || '';
    return parseDoc(document, oem);
  }

  // BULK: fetch-loop a list of OEMs same-origin (your cookies) and collect ALL
  // options for each — no page navigation. Usage: RA_HARVEST.bulk(['oem1',...])
  async function bulk(oems, delayMs = 700) {
    const all = [];
    for (const oem of oems) {
      try {
        const r = await fetch('/en/partsearch/?partnum=' + encodeURIComponent(oem), { credentials: 'include' });
        const html = await r.text();
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const rows = parseDoc(doc, oem);
        all.push(...rows);
        console.log(`[RA] ${oem}: ${rows.length} options`);
      } catch (e) { console.warn('[RA]', oem, String(e).slice(0, 60)); }
      await new Promise((s) => setTimeout(s, delayMs));
    }
    window.RA_HARVEST.parts = all;
    console.log(`%c[RA] bulk done: ${all.length} total options from ${oems.length} OEMs — run RA_HARVEST.send()`, 'color:green;font-weight:bold');
    return all.length;
  }

  async function send(parts) {
    if (!SECRET) { console.warn('%c[RA] set the secret first:  RA_HARVEST.auth("<COLLECT_SECRET>")', 'color:red'); return; }
    for (let i = 0; i < parts.length; i += CHUNK) {
      const chunk = parts.slice(i, i + CHUNK);
      const done = i + CHUNK >= parts.length;
      const r = await postJSON(RELAY, { brand: 'rockauto', parts: chunk, done, secret: SECRET });
      const j = await r.json().catch(() => ({ status: r.status }));
      console.log(`[RA] sent ${chunk.length} (done=${done}) ->`, j.status || r.status, j.import_pid ? 'pid=' + j.import_pid : '');
      if (done) console.log('✅ RockAuto price import triggered.');
    }
  }

  // Pull a batch of UNPRICED OEM numbers from our backend (US-friendly brands by
  // default). Requires the secret. Usage: await RA_HARVEST.feed(200)
  async function feed(limit = 200, brands = '') {
    if (!SECRET) { console.warn('%c[RA] set the secret first:  RA_HARVEST.auth("<COLLECT_SECRET>")', 'color:red'); return []; }
    const r = await postJSON(FEED, { secret: SECRET, limit, brands });
    const j = await r.json().catch(() => ({}));
    console.log(`[RA] feed: ${j.count || 0} unpriced OEMs (wrapped=${j.wrapped})`);
    return j.oems || [];
  }

  // One-click: fetch a batch of unpriced OEMs -> price them on RockAuto -> send.
  // Repeat as many rounds as you like: await RA_HARVEST.autorun(5, 200)
  async function autorun(rounds = 1, batch = 200, brands = '') {
    for (let n = 0; n < rounds; n++) {
      const oems = await feed(batch, brands);
      if (!oems.length) { console.log('[RA] feed empty — swept the catalog, stopping.'); break; }
      await bulk(oems);
      await send(window.RA_HARVEST.parts);
      console.log(`%c[RA] round ${n + 1}/${rounds} done`, 'color:green;font-weight:bold');
    }
  }

  const parts = extract();
  console.log(`%c[RockAuto harvester] found ${parts.length} priced parts (DRY RUN — nothing sent yet)`, 'color:green;font-weight:bold');
  console.table(parts.slice(0, 25));
  window.RA_HARVEST = {
    parts,
    auth: (s) => { SECRET = (s || '').trim(); console.log('[RA] secret set (' + SECRET.length + ' chars)'); },
    run: () => { const p = extract(); console.table(p.slice(0, 25)); window.RA_HARVEST.parts = p; return p.length; },
    bulk,
    feed,
    autorun,
    send: () => send(window.RA_HARVEST.parts),
  };
  console.log('%cAuth once:  RA_HARVEST.auth("<COLLECT_SECRET>")', 'color:blue;font-weight:bold');
  console.log('%cThen one-click continuous:  await RA_HARVEST.autorun(5, 200)', 'color:blue;font-weight:bold');
  console.log('%cOr manual:  RA_HARVEST.send()  after checking the dry-run table', 'color:blue');
})();
