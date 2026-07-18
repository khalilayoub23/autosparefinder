import { useState } from 'react'
import { Copy, Check, KeyRound, Search, Car, Boxes, Building2, ShieldCheck, Zap } from 'lucide-react'

const BASE = 'https://autosparefinder.co.il/api/public/v1'

function Code({ children }) {
  const [copied, setCopied] = useState(false)
  const text = String(children)
  const copy = () => { navigator.clipboard?.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) }) }
  return (
    <div className="relative group">
      <pre dir="ltr" className="overflow-x-auto rounded-xl bg-[#0b1f3a] text-slate-100 text-[12.5px] leading-relaxed p-4 pr-12 font-mono">{text}</pre>
      <button onClick={copy} aria-label="Copy"
        className="absolute top-2.5 right-2.5 rounded-lg bg-white/10 hover:bg-white/20 p-1.5 text-slate-200 transition">
        {copied ? <Check className="h-4 w-4 text-emerald-400" /> : <Copy className="h-4 w-4" />}
      </button>
    </div>
  )
}

function Method({ m }) {
  const c = { GET: 'bg-brand-blue/15 text-brand-600 ring-brand-blue/30' }[m] || 'bg-slate-100 text-slate-600'
  return <span className={`inline-block rounded-md px-2 py-0.5 text-[11px] font-bold tracking-wide ring-1 ${c}`}>{m}</span>
}

function Endpoint({ method = 'GET', path, icon: Icon, title, desc, params, example, response }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      <div className="flex items-start gap-3 p-5 border-b border-slate-100">
        <div className="mt-0.5 rounded-lg bg-brand-50 p-2 text-brand-600"><Icon className="h-5 w-5" /></div>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Method m={method} />
            <code dir="ltr" className="text-[13px] font-mono text-brand-navy break-all">{path}</code>
          </div>
          <h3 className="mt-1 font-bold text-brand-navy">{title}</h3>
          <p className="text-sm text-slate-500">{desc}</p>
        </div>
      </div>
      <div className="p-5 space-y-4">
        {params?.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-left text-slate-400 text-[12px] uppercase tracking-wide">
                <th className="pb-2 pr-4 font-semibold">Param</th><th className="pb-2 pr-4 font-semibold">Type</th><th className="pb-2 font-semibold">Notes</th>
              </tr></thead>
              <tbody className="text-slate-600">
                {params.map((p) => (
                  <tr key={p[0]} className="border-t border-slate-100">
                    <td className="py-2 pr-4"><code className="text-brand-600">{p[0]}</code></td>
                    <td className="py-2 pr-4 text-slate-400">{p[1]}</td>
                    <td className="py-2">{p[2]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {example && (<div><p className="mb-1.5 text-[12px] font-semibold uppercase tracking-wide text-slate-400">Example request</p><Code>{example}</Code></div>)}
        {response && (<div><p className="mb-1.5 text-[12px] font-semibold uppercase tracking-wide text-slate-400">Example response</p><Code>{response}</Code></div>)}
      </div>
    </div>
  )
}

export default function DeveloperApi() {
  return (
    <div dir="ltr" className="min-h-screen bg-gradient-to-b from-slate-50 to-slate-100">
      {/* Hero */}
      <header className="bg-[#0b1f3a] text-white">
        <div className="mx-auto max-w-4xl px-5 py-14 sm:py-20">
          <a href="/" className="inline-flex items-center gap-2 text-slate-300 hover:text-white text-sm mb-6">← Back to AutoSpareFinder</a>
          <div className="inline-flex items-center gap-2 rounded-full bg-brand-blue/15 px-3 py-1 text-[12px] font-semibold text-brand-blue ring-1 ring-brand-blue/30">
            <Zap className="h-3.5 w-3.5" /> Partner API · v1
          </div>
          <h1 className="mt-4 text-3xl sm:text-4xl font-black tracking-tight">AutoSpareFinder Developer API</h1>
          <p className="mt-3 max-w-2xl text-slate-300 leading-relaxed">
            Connect your site or app to millions of car parts with fitment and customer-ready prices —
            through one small, stable REST API. You get exactly what you need to show and sell parts,
            and nothing internal.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <a href="mailto:support@autosparefinder.co.il?subject=API%20key%20request"
               className="inline-flex items-center gap-2 rounded-xl bg-brand-blue px-5 py-2.5 font-semibold text-[#0b1f3a] hover:brightness-110 transition">
              <KeyRound className="h-4 w-4" /> Request an API key
            </a>
            <a href="/docs" target="_blank" rel="noreferrer"
               className="inline-flex items-center gap-2 rounded-xl bg-white/10 px-5 py-2.5 font-semibold text-white hover:bg-white/20 transition">
              Interactive reference (Swagger)
            </a>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-5 py-12 space-y-10">
        {/* Quick start */}
        <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-lg font-bold text-brand-navy">Quick start</h2>
          <p className="mt-1 text-sm text-slate-500">Every endpoint except <code className="text-brand-600">/health</code> needs your key in the <code className="text-brand-600">X-API-Key</code> header. Base URL:</p>
          <div className="mt-3"><Code>{BASE}</Code></div>
          <div className="mt-3"><Code>{`curl -H "X-API-Key: asf_live_xxxxx" \\
  "${BASE}/search?q=oil%20filter&manufacturer=Toyota&limit=5"`}</Code></div>
          <div className="mt-4 grid gap-3 sm:grid-cols-3 text-sm">
            <div className="flex items-start gap-2 rounded-xl bg-brand-50 p-3"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" /><span className="text-slate-600"><b className="text-brand-navy">Only what you need.</b> No supplier names, no cost, no margins.</span></div>
            <div className="flex items-start gap-2 rounded-xl bg-brand-50 p-3"><Building2 className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" /><span className="text-slate-600"><b className="text-brand-navy">Customer-ready prices.</b> ILS, VAT applied per law (18% local, 0% foreign).</span></div>
            <div className="flex items-start gap-2 rounded-xl bg-brand-50 p-3"><Zap className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" /><span className="text-slate-600"><b className="text-brand-navy">Fast.</b> Fitment & search in well under a second.</span></div>
          </div>
        </section>

        {/* Endpoints */}
        <section className="space-y-5">
          <h2 className="text-lg font-bold text-brand-navy">Endpoints</h2>

          <Endpoint icon={Search} path="/api/public/v1/search" title="Search parts"
            desc="Free-text search and/or filter by brand and category. Provide at least q or manufacturer."
            params={[['q','string','Free text (part name / OEM), relevance-ranked'],['manufacturer','string','Car brand, e.g. Toyota'],['category','string','Category slug, e.g. brakes, filters'],['limit','int','1–50 (default 20)'],['offset','int','0–1000 (default 0)']]}
            example={`curl -H "X-API-Key: $KEY" \\
  "${BASE}/search?q=brake%20pads&manufacturer=Toyota&limit=5"`}
            response={`{
  "count": 1, "limit": 5, "offset": 0,
  "results": [
    {
      "part_id": "00437f73-2d26-4ba5-9320-23e9fe136e88",
      "oem_number": "9G33-6714-AA",
      "name": "Oil Filter", "name_he": "מסנן שמן",
      "manufacturer": "Jaguar", "category": "filters",
      "available": true,
      "price": { "amount": 244.82, "vat": 0.0, "total": 244.82,
                 "currency": "ILS", "vat_included": false }
    }
  ]
}`} />

          <Endpoint icon={Boxes} path="/api/public/v1/parts/{part_id}" title="Get one part"
            desc="Look up a single part by its part_id (as returned by search or fitment)."
            example={`curl -H "X-API-Key: $KEY" \\
  "${BASE}/parts/00437f73-2d26-4ba5-9320-23e9fe136e88"`} />

          <Endpoint icon={Car} path="/api/public/v1/fitment" title="Parts that fit a vehicle"
            desc="Find parts compatible with a specific make, model and (optional) year."
            params={[['make','string','required — e.g. Toyota'],['model','string','required — e.g. Corolla'],['year','int','optional — e.g. 2018'],['category','string','optional'],['limit / offset','int','as above']]}
            example={`curl -H "X-API-Key: $KEY" \\
  "${BASE}/fitment?make=Toyota&model=Corolla&year=2018"`} />

          <Endpoint icon={Building2} path="/api/public/v1/manufacturers" title="List car brands"
            desc="All car brands present in the catalog."
            example={`curl -H "X-API-Key: $KEY" "${BASE}/manufacturers"`} />
        </section>

        {/* Schema + errors */}
        <section className="grid gap-5 md:grid-cols-2">
          <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <h2 className="text-lg font-bold text-brand-navy">Part object</h2>
            <p className="mt-1 text-sm text-slate-500">The only fields returned for a part.</p>
            <div className="mt-3"><Code>{`{
  "part_id":      "uuid",
  "oem_number":   "string | null",
  "name":         "string",
  "name_he":      "string | null",
  "manufacturer": "string",
  "category":     "string | null",
  "barcode":      "string | null",
  "available":    true,
  "price": {                 // null when unavailable
    "amount": 145.0,         // net (before VAT)
    "vat":    26.1,          // 18% local, 0% foreign
    "total":  171.1,         // customer pays (excl. shipping)
    "currency": "ILS",
    "vat_included": true
  }
}`}</Code></div>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <h2 className="text-lg font-bold text-brand-navy">Errors & limits</h2>
            <table className="mt-3 w-full text-sm">
              <tbody className="text-slate-600">
                {[['400','Missing required params'],['401','Missing / invalid / inactive API key'],['404','Part not found'],['429','Rate limit exceeded (per-key, per minute)']].map((r) => (
                  <tr key={r[0]} className="border-t border-slate-100 first:border-0">
                    <td className="py-2 pr-3"><code className="text-brand-600 font-bold">{r[0]}</code></td><td className="py-2">{r[1]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-4 rounded-xl bg-brand-50 p-3 text-sm text-slate-600">
              <b className="text-brand-navy">Rate limit.</b> Each key has a per-minute limit (default 60/min).
              Cache where you can and stay within it. Prices reflect the cheapest available offer and change with stock.
            </div>
          </div>
        </section>

        {/* CTA */}
        <section className="rounded-2xl bg-[#0b1f3a] p-8 text-center text-white">
          <h2 className="text-xl font-bold">Ready to build?</h2>
          <p className="mt-1 text-slate-300">Tell us about your use case and we'll issue a key with the right limits.</p>
          <a href="mailto:support@autosparefinder.co.il?subject=API%20key%20request"
             className="mt-5 inline-flex items-center gap-2 rounded-xl bg-brand-blue px-6 py-3 font-semibold text-[#0b1f3a] hover:brightness-110 transition">
            <KeyRound className="h-4 w-4" /> Request an API key
          </a>
        </section>
      </main>

      <footer className="border-t border-slate-200 py-8 text-center text-[12px] text-slate-400">
        © {new Date().getFullYear()} AutoSpareFinder · <a href="/" className="hover:text-slate-600">Home</a> · <a href="/terms" className="hover:text-slate-600">Terms</a>
      </footer>
    </div>
  )
}
