/* =====================================================================
 * Amayama browser harvester (2026-08-06 — REPLACES the FlareSolverr path).
 *
 * Script:  amayama_browser_harvester.js
 * Purpose: Harvest Amayama (amayama.com) genuine Japanese/Asian OEM part
 *          prices + real IL/int'l shipping for our unpriced catalog.
 * Process: Run in the console on a REAL, already-logged-in amayama.com tab.
 *          Pulls a batch of unpriced OEMs from our own feed endpoint, fetches
 *          each part's page SAME-ORIGIN (fetch with credentials, exactly as
 *          the site's own JS would), parses the offer table, relays matches
 *          to our /collect endpoint (brand='amayama') which runs
 *          importers/amayama_price_import.py.
 * Data Imported/Modified: supplier_parts (Amayama offer), parts_catalog
 *          pricing (price-fill only, never overwrites an existing IL price),
 *          parts_catalog.specifications->'amayama_options'.
 * Data Sources: https://www.amayama.com/en/part/<brand>/<oem-no-dash>
 * Missing Data Delegation: n/a (browser-only; no server-side path currently
 *          works — see note below).
 * Last Updated: 2026-08-06
 *
 * WHY THIS EXISTS (root cause, 2026-08-06): the SERVER-SIDE FlareSolverr
 * harvester (amayama_flaresolverr_harvester.py, disabled via
 * AMAYAMA_HARVEST_ENABLED=0 since 2026-08-02) was re-tested live and found to
 * be permanently blocked — Amayama's Cloudflare "Managed Challenge" never
 * resolves for ANY Chrome-DevTools-Protocol-driven browser (FlareSolverr's
 * internal browser AND a fresh vanilla Playwright/Chromium instance both got
 * stuck on "Performing security verification" indefinitely, 100% reproducible
 * across fresh sessions — this is Cloudflare's known CDP `Runtime.enable`
 * fingerprint detection, not a solvable JS challenge). A REAL, non-CDP,
 * already-authenticated browser tab (this script) sails through with ZERO
 * interstitial — proven live: 90915-YZZD4 → 14 real offer rows, 04465-33471
 * → 10 real offer rows, both via plain same-origin `fetch()`, no captcha.
 * CONCLUSION: Amayama's wall is specifically anti-automation-framework, not
 * anti-scraping in general — a genuine browser session goes around it clean.
 *
 * USAGE:
 *   1. Open https://www.amayama.com/en/ in a real Chrome tab, make sure
 *      you're logged into the account (Balance/Orders/Profile visible).
 *   2. Paste this whole script in the console.
 *   3. Auth once:            AMAYAMA.auth('<COLLECT_SECRET>')
 *   4. One-click continuous: await AMAYAMA.autorun(20, 40)
 *      (20 rounds x 40 OEMs/round; stops early if the feed empties)
 *   5. Leave the tab open — it's the harvester. Closing it stops the loop.
 *      Re-run `await AMAYAMA.autorun(...)` any time to resume; the feed's
 *      own cursor tracks progress across runs so nothing is re-searched
 *      needlessly.
 * ===================================================================== */
(function () {
  const BASE  = 'https://autosparefinder.co.il/api/v1/system';
  const RELAY = BASE + '/collect';
  const FEED  = BASE + '/unpriced-oems';
  const CHUNK = 200;
  const JP_BRANDS = 'toyota,lexus,honda,nissan,mazda,subaru,mitsubishi,infiniti,acura,suzuki,daihatsu';
  // Cross-origin (amayama.com -> our backend): CORS "simple request" pattern,
  // same as rockauto_browser_harvester.js — Content-Type text/plain + secret
  // in the JSON body avoids a preflight our CORSMiddleware would 400 on.
  let SECRET = '';
  const postJSON = (url, obj) => fetch(url, {
    method: 'POST', credentials: 'omit',
    headers: { 'Content-Type': 'text/plain' },
    body: JSON.stringify(obj),
  });

  // Parse ONE Amayama part page (already-fetched `doc`) into every warehouse
  // offer row. A search that matches MULTIPLE cross-reference groups (OEM +
  // aftermarket brands) repeats the header row per group with DIFFERENT
  // column counts (6/7/8 cells — "From" vs "Original Number, From" vs "Brand
  // / From"), so a fixed column index is unsafe. Instead: a row is a DATA row
  // iff it contains a `.shipping_price` cell (verified 2026-08-06 — present on
  // every real offer, absent on every header). Price = the cell immediately
  // before shipping_price; warehouse = the row's first cell; ETA = the
  // `.shipping_date` cell; stock = the cell right after it.
  function parseDoc(doc, oem, finalUrl) {
    const h1 = doc.querySelector('h1');
    const name = (h1 ? h1.innerText : '').trim();
    // URL path: /en/part/<brand>/<oem-no-dash-lowercase>. MUST come from the
    // fetch's OWN response URL (finalUrl), never doc.baseURI/location — a
    // DOMParser-parsed document has no real navigation, so baseURI silently
    // falls back to the calling PAGE's URL and leaks the previous OEM's
    // brand/part_num onto every subsequent row (found live 2026-08-06).
    const m = (new URL(finalUrl || location.href)).pathname.match(/\/en\/part\/([^/]+)\/([^/?#]+)/);
    const brand = m ? decodeURIComponent(m[1]) : '';
    const partNum = m ? decodeURIComponent(m[2]) : '';
    const rows = doc.querySelectorAll('tr.part-table__row');
    const out = [];
    rows.forEach((row) => {
      const shipEl = row.querySelector('.shipping_price');
      if (!shipEl) return;                                   // header row — skip
      const cells = [...row.querySelectorAll('td,th')];
      const shipIdx = cells.indexOf(shipEl.closest('td,th'));
      if (shipIdx < 1) return;
      const warehouse = (cells[0].innerText || '').trim();
      const price = parseFloat((cells[shipIdx - 1].innerText || '').replace(/,/g, ''));
      const shipping = parseFloat((shipEl.innerText || '').replace(/,/g, ''));
      if (!(price > 0)) return;
      out.push({
        oem, name, brand, part_num: partNum, warehouse,
        price_usd: price,
        shipping_usd: (shipping > 0) ? shipping : null,
        part_type: brand && partNum && !partNum.toUpperCase().startsWith(brand.slice(0, 2).toUpperCase()) ? 'analog' : 'genuine',
      });
    });
    return out;
  }

  // Sequential (Amayama penalizes concurrency — see FIXES_TRACKER 2026-07-12:
  // concurrency 8/3/2 -> 91/48/67% blocked). delayMs paces like a human.
  async function bulk(oems, delayMs = 900) {
    const all = [];
    for (const oem of oems) {
      try {
        const clean = oem.replace(/[-\s]/g, '');
        const r = await fetch('/en/find?q=' + encodeURIComponent(clean), { credentials: 'same-origin' });
        const html = await r.text();
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const rows = parseDoc(doc, oem, r.url);
        all.push(...rows);
        console.log(`[AMY] ${oem}: ${rows.length} offers`);
      } catch (e) { console.warn('[AMY]', oem, String(e).slice(0, 80)); }
      await new Promise((s) => setTimeout(s, delayMs + Math.random() * 400));
    }
    window.AMAYAMA.parts = all;
    console.log(`%c[AMY] bulk done: ${all.length} total offers from ${oems.length} OEMs — run AMAYAMA.send()`, 'color:green;font-weight:bold');
    return all.length;
  }

  async function send(parts) {
    if (!SECRET) { console.warn('%c[AMY] set the secret first:  AMAYAMA.auth("<COLLECT_SECRET>")', 'color:red'); return; }
    if (!parts || !parts.length) { console.log('[AMY] nothing to send'); return; }
    for (let i = 0; i < parts.length; i += CHUNK) {
      const chunk = parts.slice(i, i + CHUNK);
      const done = i + CHUNK >= parts.length;
      const r = await postJSON(RELAY, { brand: 'amayama', parts: chunk, done, secret: SECRET });
      const j = await r.json().catch(() => ({ status: r.status }));
      console.log(`[AMY] sent ${chunk.length} (done=${done}) ->`, j.status || r.status, j.import_pid ? 'pid=' + j.import_pid : '');
      if (done) console.log('✅ Amayama price import triggered.');
    }
  }

  async function feed(limit = 40, brands = JP_BRANDS) {
    if (!SECRET) { console.warn('%c[AMY] set the secret first:  AMAYAMA.auth("<COLLECT_SECRET>")', 'color:red'); return []; }
    const r = await postJSON(FEED, { secret: SECRET, source: 'amayama_browser', limit, brands });
    const j = await r.json().catch(() => ({}));
    console.log(`[AMY] feed: ${j.count || (j.oems || []).length || 0} unpriced OEMs`);
    return j.oems || [];
  }

  // Fire-and-forget friendly: stats live on window.AMAYAMA.stats so a caller
  // can kick off autorun() WITHOUT awaiting it (a long run easily exceeds a
  // single tool-call's time budget) and poll progress separately.
  async function autorun(rounds = 10, batch = 40) {
    window.AMAYAMA.running = true;
    window.AMAYAMA.stats = { roundsDone: 0, roundsTarget: rounds, oemsSeen: 0, offersFound: 0, startedAt: Date.now() };
    for (let n = 0; n < rounds; n++) {
      const oems = await feed(batch);
      if (!oems.length) { console.log('[AMY] feed empty — swept the current batch, stopping.'); break; }
      await bulk(oems);
      await send(window.AMAYAMA.parts);
      window.AMAYAMA.stats.roundsDone = n + 1;
      window.AMAYAMA.stats.oemsSeen += oems.length;
      window.AMAYAMA.stats.offersFound += window.AMAYAMA.parts.length;
      console.log(`%c[AMY] round ${n + 1}/${rounds} done`, 'color:green;font-weight:bold');
    }
    window.AMAYAMA.running = false;
    console.log('%c[AMY] autorun finished — call AMAYAMA.autorun(...) again to resume later.', 'color:blue;font-weight:bold');
  }

  window.AMAYAMA = {
    parts: [],
    running: false,
    stats: null,
    auth: (s) => { SECRET = (s || '').trim(); console.log('[AMY] secret set (' + SECRET.length + ' chars)'); },
    feed, bulk, autorun,
    send: () => send(window.AMAYAMA.parts),
  };
  console.log('%c[Amayama browser harvester loaded]', 'color:blue;font-weight:bold');
  console.log('%cAuth once:  AMAYAMA.auth("<COLLECT_SECRET>")', 'color:blue;font-weight:bold');
  console.log('%cThen one-click continuous:  await AMAYAMA.autorun(20, 40)', 'color:blue;font-weight:bold');
})();
