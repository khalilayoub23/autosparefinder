import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import { useCartStore } from '../stores/cartStore'
import { useI18n, LANGS, LANG_NAMES, LANG_FLAGS } from '../i18n'

/* ── WhatsApp SVG icon ─────────────────────────────────────────────── */
function WhatsAppIcon({ className = "w-4 h-4" }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M12 2.4a9.6 9.6 0 0 0-8.18 14.63L3 21l4.17-1.1A9.6 9.6 0 1 0 12 2.4Z" fill="#25D366" />
      <path d="m8.8 7.74-.58.05a1 1 0 0 0-.71.42c-.48.68-1.09 1.92-.4 3.8.9 2.44 3.46 4.88 5.73 5.76 1.18.46 2.11.15 2.75-.2.35-.19.56-.54.6-.93l.04-.55a.68.68 0 0 0-.43-.68l-2.18-.8a.68.68 0 0 0-.77.22l-.43.57a.58.58 0 0 1-.64.19 6.35 6.35 0 0 1-3.12-3.1.58.58 0 0 1 .18-.65l.57-.44a.68.68 0 0 0 .23-.77l-.81-2.16a.68.68 0 0 0-.63-.43Z" fill="#fff" />
    </svg>
  )
}

/* ── Menu items (label via i18n key) ────────────────────────────────── */
const menuItems = [
  { key: "nav.home", href: "/" },
  { key: "nav.categories", href: "#categories", hasChevron: true },
  { key: "nav.catalog", href: "/parts" },
  { key: "nav.quote", href: "/parts" },
  { key: "nav.how", href: "#how" },
  { key: "nav.about", href: "#about" },
  { key: "nav.support", href: "/chat" },
]

/* ── Hero search tabs (id drives logic, key drives label) ───────────── */
const heroTabs = [
  { id: "vin",     key: "hero.tab.vin",     icon: "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z", badge: true },
  { id: "oem",     key: "hero.tab.oem",     icon: "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" },
  { id: "sku",     key: "hero.tab.sku",     icon: "M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z" },
  { id: "vehicle", key: "hero.tab.vehicle", icon: "M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" },
]

/* ── How It Works steps ─────────────────────────────────────────────── */
const steps = [
  { icon: "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z", key: "how.step1", keyD: "how.step1d" },
  { icon: "M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z", key: "how.step2", keyD: "how.step2d" },
  { icon: "M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 11-4 0 2 2 0 014 0z", key: "how.step3", keyD: "how.step3d" },
  { icon: "M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0zM15 11a3 3 0 11-6 0 3 3 0 016 0z", key: "how.step4", keyD: "how.step4d" },
]

/* ── Robot/Bot SVG icon for AI section ──────────────────────────────── */
function BotIcon({ className = "w-8 h-8" }) {
  return (
    <svg className={className} viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="12" y="22" width="40" height="30" rx="6" fill="#2563eb" fillOpacity="0.3" stroke="#60a5fa" strokeWidth="2"/>
      <rect x="20" y="32" width="8" height="8" rx="2" fill="#60a5fa"/>
      <rect x="36" y="32" width="8" height="8" rx="2" fill="#60a5fa"/>
      <rect x="26" y="43" width="12" height="3" rx="1.5" fill="#93c5fd"/>
      <line x1="32" y1="14" x2="32" y2="22" stroke="#60a5fa" strokeWidth="2.5" strokeLinecap="round"/>
      <circle cx="32" cy="11" r="3.5" fill="#2563eb" stroke="#60a5fa" strokeWidth="2"/>
      <line x1="12" y1="34" x2="5" y2="38" stroke="#60a5fa" strokeWidth="2.5" strokeLinecap="round"/>
      <line x1="52" y1="34" x2="59" y2="38" stroke="#60a5fa" strokeWidth="2.5" strokeLinecap="round"/>
    </svg>
  )
}

/* ── Category cards (nameKey, count prefix, image, isMore) ──────────── */
const categories = [
  ["cat.engine",       "25,000+", "/part-family/cutouts/engine.png"],
  ["cat.brakes",       "18,000+", "/part-family/cutouts/brakes.png"],
  ["cat.suspension",   "12,000+", "/part-family/cutouts/suspension-steering.png"],
  ["cat.electrical",   "20,000+", "/part-family/cutouts/electrical-sensors.png"],
  ["cat.body",         "15,000+", "/part-family/cutouts/body-exterior.png"],
  ["cat.transmission", "8,000+",  "/part-family/cutouts/gearbox.png"],
  ["cat.cooling",      "7,000+",  "/part-family/cutouts/cooling.png"],
  ["cat.filters",      "6,000+",  "/part-family/cutouts/filters.png"],
  ["cat.exhaust",      "9,000+",  "/part-family/cutouts/exhaust.png"],
  ["cat.more",         "",        "", true],
]

/* ── Trust bar items (titleKey, subKey, icon) ───────────────────────── */
const trustItems = [
  ["trust.suppliers", "trust.suppliersSub", "M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"],
  ["trust.prices",    "trust.pricesSub",    "M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z"],
  ["trust.delivery",  "trust.deliverySub",  "M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4"],
  ["trust.payments",  "trust.paymentsSub",  "M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"],
  ["trust.expert",    "trust.expertSub",    "M18.364 5.636l-3.536 3.536m0 5.656l3.536 3.536M9.172 9.172L5.636 5.636m3.536 9.192l-3.536 3.536M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-5 0a4 4 0 11-8 0 4 4 0 018 0z"],
]

const featureCards = [
  ["feat.compat",  "feat.compatD",  "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"],
  ["feat.cond",    "feat.condD",    "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"],
  ["feat.ship",    "feat.shipD",    "M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9"],
  ["feat.returns", "feat.returnsD", "M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"],
]

export default function LandingPage() {
  const navigate = useNavigate()
  const { user } = useAuthStore()
  const { totals } = useCartStore()
  const { t, dir, lang, setLang } = useI18n()
  const cartCount = (() => { try { return totals().count } catch { return 0 } })()

  /* nav search */
  const [navQuery, setNavQuery] = useState("")
  /* hero search */
  const [activeTab, setActiveTab] = useState("vin")
  const [heroQuery, setHeroQuery] = useState("")
  const [loading, setLoading] = useState(false)

  const handleNavSearch = () => {
    const q = navQuery.trim()
    if (q) navigate(`/parts?search=${encodeURIComponent(q)}`)
  }

  const handleHeroSearch = () => {
    const q = heroQuery.trim()
    if (!q) return
    navigate(`/parts?search=${encodeURIComponent(q)}&mode=${activeTab}`)
  }

  return (
    <div dir={dir} className="min-h-screen bg-[#f4f7fd] text-slate-900 font-sans">

      {/* ═══════════════════════════════════════════════════════════════
          HEADER
      ═══════════════════════════════════════════════════════════════ */}
      <header className="bg-[#0d1524] text-white font-sans">

        {/* Top info bar */}
        <div className="border-b border-white/10 text-[12px] text-slate-300 bg-[#080e1c]">
          <div className="max-w-[1400px] mx-auto px-4 py-2.5 flex md:items-center flex-col md:flex-row justify-between gap-3">
            <div className="flex items-center gap-2">
              <svg className="w-4 h-4 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              <p>{t('topbar.tagline')}</p>
            </div>
            <div className="flex items-center gap-6 font-medium">
              <a href="/chat" className="hover:text-white cursor-pointer transition-colors">{t('nav.support')}</a>
              <a href="/orders" className="hover:text-white cursor-pointer transition-colors">{t('topbar.trackOrder')}</a>
              <a href="https://wa.me/972532426920" target="_blank" rel="noreferrer" className="flex items-center gap-1.5 cursor-pointer font-semibold text-slate-200 hover:text-white transition-colors">
                <WhatsAppIcon className="w-4 h-4" />
                WhatsApp
              </a>
              {/* Language selector */}
              <details className="relative" aria-label={t('lang.label')}>
                <summary className="list-none cursor-pointer flex items-center gap-1.5 rounded-md border border-white/10 px-2 py-1 hover:bg-white/10 transition-colors">
                  <img src={`https://flagcdn.com/w20/${LANG_FLAGS[lang]}.png`} alt={LANG_NAMES[lang]} className="w-4 h-auto rounded-sm" />
                  <span className="text-[12px] font-semibold text-slate-200">{LANG_NAMES[lang]}</span>
                  <svg className="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                </summary>
                <div className="absolute ltr:right-0 rtl:left-0 mt-2 w-36 rounded-md border border-white/15 bg-[#021737] shadow-lg overflow-hidden z-20">
                  {LANGS.map((l) => (
                    <button
                      key={l}
                      onClick={(e) => { setLang(l); const d = e.currentTarget.closest('details'); if (d) d.open = false }}
                      className="w-full flex items-center gap-2 px-2.5 py-1.5 text-[12px] hover:bg-white/10 transition-colors text-start"
                    >
                      <img src={`https://flagcdn.com/w20/${LANG_FLAGS[l]}.png`} alt={LANG_NAMES[l]} className="w-4 h-auto rounded-sm" />
                      <span>{LANG_NAMES[l]}</span>
                    </button>
                  ))}
                </div>
              </details>
            </div>
          </div>
        </div>

        {/* Main nav: logo + search + icons */}
        <div className="max-w-[1400px] mx-auto px-4 py-2.5 md:py-3 flex items-center justify-between gap-4 md:gap-5">
          <a href="/" className="flex-shrink-0">
            <img
              src="/logo-tests/autosparefinder-logo-header.svg"
              alt="AutoSpare Finder"
              className="h-[86px] md:h-[94px] w-auto max-w-[128px] md:max-w-[144px] object-contain opacity-[0.95] drop-shadow-[0_5px_12px_rgba(0,0,0,0.22)]"
            />
          </a>

          <div className="flex-1 hidden md:flex items-center bg-white rounded-md overflow-hidden max-w-[700px] h-[52px] shadow-lg">
            <input
              value={navQuery}
              onChange={(e) => setNavQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleNavSearch()}
              className="flex-1 px-5 py-2 text-[15px] text-slate-800 outline-none h-full placeholder:text-slate-400"
              placeholder={t('search.navPlaceholder')}
            />
            <div className="h-2/3 w-[1px] bg-slate-200"></div>
            <select className="px-3 h-full text-[13px] text-slate-600 outline-none bg-transparent cursor-pointer font-medium hover:bg-slate-50 transition-colors">
              <option>{t('search.allCategories')}</option>
            </select>
            <button onClick={handleNavSearch} className="h-full bg-[#2563eb] hover:bg-[#1d4ed8] px-6 transition-colors flex items-center justify-center">
              <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </button>
          </div>

          <div className="hidden lg:flex items-center gap-4 text-[13px] font-medium text-slate-100 flex-shrink-0">
            <a href="/chat" className="flex items-center gap-3 cursor-pointer hover:text-white transition-colors group">
              <svg className="w-6 h-6 text-slate-300 group-hover:text-white transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
              </svg>
              <div className="flex flex-col leading-tight">
                <span className="font-bold">{t('account.chatWithUs')}</span>
                <span className="text-[12px] text-slate-400">{t('account.online')}</span>
              </div>
            </a>
            <a href={user ? "/account" : "/login"} className="flex items-center gap-3 cursor-pointer hover:text-white transition-colors group">
              <svg className="w-6 h-6 text-slate-300 group-hover:text-white transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
              </svg>
              <div className="flex flex-col leading-tight">
                <span className="font-bold">{user ? t('account.myAccount') : t('account.signIn')}</span>
                <span className="text-[12px] text-slate-400">{user ? user.email?.split('@')[0] : t('account.myAccount')}</span>
              </div>
            </a>
            <a href="/cart" className="flex items-center gap-3 cursor-pointer hover:text-white transition-colors group relative">
              <div className="relative">
                <svg className="w-6 h-6 text-slate-300 group-hover:text-white transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 11-4 0 2 2 0 014 0z" />
                </svg>
                <div className="absolute -top-1.5 ltr:-right-2 rtl:-left-2 bg-[#2563eb] text-white text-[11px] w-5 h-5 rounded-full flex items-center justify-center font-bold shadow-sm">{cartCount > 99 ? '99+' : cartCount}</div>
              </div>
              <span className="font-bold">{t('account.cart')}</span>
            </a>
          </div>
        </div>

        {/* Sub-nav */}
        <nav className="bg-[#0d1524] border-t border-white/5 border-b border-white/5">
          <div className="max-w-[1400px] mx-auto px-4 flex items-center gap-2 overflow-x-auto whitespace-nowrap text-[15px] font-semibold">
            {menuItems.map((item, idx) => (
              <a
                key={item.key}
                href={item.href}
                className={`px-6 py-3.5 transition-colors flex items-center gap-1.5 ${
                  idx === 0
                    ? "bg-[#2563eb] text-white rounded-t-sm"
                    : "text-slate-300 hover:text-white hover:bg-white/5 rounded-t-sm"
                }`}
              >
                {t(item.key)}
                {item.hasChevron && (
                  <svg className="w-4 h-4 ml-0.5 opacity-70" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                )}
              </a>
            ))}
          </div>
        </nav>
      </header>

      {/* ═══════════════════════════════════════════════════════════════
          HERO SECTION
      ═══════════════════════════════════════════════════════════════ */}
      <section className="bg-[#070e1d] relative overflow-hidden">
        <div className="absolute inset-0 z-0">
          <div className="absolute inset-0 bg-gradient-to-br from-[#0a1525] via-[#0d1b35] to-[#091226] opacity-95"></div>
          <div className="absolute top-[10%] ltr:right-[-5%] rtl:left-[-5%] w-[65%] max-w-[900px] h-full bg-[radial-gradient(ellipse_at_center,rgba(37,99,235,0.18),transparent_55%)] pointer-events-none"></div>
        </div>

        <div className="max-w-[1300px] mx-auto px-4 py-6 lg:py-12">
          <div className="grid md:grid-cols-2 gap-0 items-stretch relative z-10">

            {/* Left: headline + search */}
            <div className="text-white pt-4">
              <h1 className="text-[38px] md:text-[52px] lg:text-[60px] leading-[1.05] font-extrabold tracking-tight">
                {t('hero.title1')}<br />
                <span className="text-[#3b82f6]">{t('hero.fast')}</span> {t('hero.easy')} <span className="text-[#3b82f6]">{t('hero.reliable')}</span>
              </h1>
              <p className="mt-5 text-[15px] md:text-[17px] text-slate-300 max-w-lg leading-relaxed">
                {t('hero.subtitle')}
              </p>

              <div className="mt-8 rounded-2xl border border-white/5 bg-[#0a1e3f]/60 p-4 lg:p-5 shadow-2xl backdrop-blur-md">
                <div role="tablist" aria-label="Search tabs" className="flex flex-wrap gap-2 mb-3">
                  {heroTabs.map((tab) => {
                    const active = tab.id === activeTab
                    return (
                      <button
                        key={tab.id}
                        aria-label={t(tab.key)}
                        aria-pressed={active}
                        onClick={() => setActiveTab(tab.id)}
                        className={`text-[12px] md:text-[13px] rounded-lg border border-transparent flex items-center gap-1.5 px-3 lg:px-4 py-2 font-medium transition-all ${
                          active ? "bg-[#2563eb] text-white border-[#3b82f6]/50 shadow-[0_0_15px_rgba(37,99,235,0.3)]" : "bg-[#0f274e] text-slate-300 hover:text-white border-white/5 hover:bg-[#153468]"
                        }`}
                      >
                        <svg className="w-4 h-4 opacity-80" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={tab.icon} />
                        </svg>
                        {t(tab.key)}
                      </button>
                    )
                  })}
                </div>

                <div className="flex flex-col sm:flex-row gap-0 rounded-xl overflow-hidden bg-white shadow-inner focus-within:ring-2 focus-within:ring-[#3b82f6] transition-shadow mt-2 relative">
                  <div className="absolute ltr:left-4 rtl:right-4 top-1/2 -translate-y-1/2 text-slate-400">
                    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
                    </svg>
                  </div>
                  <input
                    aria-label={t('hero.searchBtn')}
                    value={heroQuery}
                    onChange={(e) => setHeroQuery(e.target.value)}
                    placeholder={activeTab === "vin" ? t('hero.vinPlaceholder') : t('hero.partPlaceholder')}
                    className="flex-1 ltr:pl-12 ltr:pr-4 rtl:pr-12 rtl:pl-4 py-4 md:text-[15px] text-slate-800 outline-none w-full font-medium"
                    onKeyDown={(e) => e.key === "Enter" && handleHeroSearch()}
                  />
                  <button
                    aria-label={t('hero.searchBtn')}
                    onClick={handleHeroSearch}
                    disabled={loading}
                    className="bg-[#2563eb] hover:bg-[#1d4ed8] px-8 py-4 text-white font-bold transition-colors whitespace-nowrap shadow-lg"
                  >
                    {t('hero.searchBtn')}
                  </button>
                </div>

                <div className="mt-4 text-[12px] text-slate-400 flex items-center flex-wrap gap-4 px-1">
                  <span className="flex items-center gap-1.5">
                    <svg className="w-3.5 h-3.5 text-emerald-400" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                    </svg>
                    {t('hero.safe')}
                  </span>
                  <span className="flex items-center gap-1.5">
                    <div className="w-1 h-1 rounded-full bg-slate-500"></div>
                    {t('hero.noStore')}
                  </span>
                </div>
              </div>
            </div>

            {/* Right: hero cutout image */}
            <div className="relative hidden md:flex items-center justify-center overflow-hidden">
              <div className="absolute inset-x-8 bottom-0 h-[58%] bg-[radial-gradient(ellipse_at_center,rgba(45,91,227,0.22),rgba(2,20,49,0)_72%)] pointer-events-none"></div>
              <img
                src="/hero-cutout.png"
                alt="Auto Parts Illustration"
                className="w-[96%] max-w-[760px] h-auto object-contain opacity-[0.98] drop-shadow-[0_20px_38px_rgba(0,0,0,0.48)] -translate-y-8 lg:-translate-y-10 select-none [mask-image:linear-gradient(to_bottom,black_0%,black_94%,transparent_100%)]"
                draggable={false}
              />
            </div>
          </div>
        </div>

        {/* Trust bar */}
        <div className="bg-[#0d1524] border-y border-white/10 relative z-20">
          <div className="max-w-[1300px] mx-auto px-4">
            <div className="grid grid-cols-2 md:grid-cols-5 divide-x divide-white/10">
              {trustItems.map(([titleKey, subKey, icon]) => (
                <div key={titleKey} className="flex flex-col items-center md:items-start text-center md:text-start py-4 px-2 lg:px-6 hover:bg-white/5 transition-colors">
                  <div className="flex items-center gap-2 lg:gap-3 mb-1 justify-center md:justify-start w-full">
                    <svg className="w-5 h-5 lg:w-6 lg:h-6 text-slate-300 stroke-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d={icon} />
                    </svg>
                    <p className="font-semibold text-[13px] md:text-[14px] text-white whitespace-nowrap">{t(titleKey)}</p>
                  </div>
                  <p className="text-[11px] md:text-[12px] text-slate-400 w-full">{t(subKey)}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          HOW IT WORKS + CATEGORIES
      ═══════════════════════════════════════════════════════════════ */}
      <section className="bg-white" id="how">
        <div className="max-w-7xl mx-auto px-4 py-12">
          <div className="grid lg:grid-cols-[280px_1fr] gap-10">

            {/* How It Works steps */}
            <div>
              <h2 className="text-xl md:text-2xl font-bold text-[#021737] mb-8">
                {t('how.title', { brand: 'AutoSpareFinder' })}
              </h2>
              <div className="relative">
                <div className="absolute ltr:left-[19px] rtl:right-[19px] top-6 bottom-6 w-px border-l-2 border-dashed border-slate-200"></div>
                <div className="space-y-7">
                  {steps.map((step, idx) => (
                    <div key={step.key} className="flex items-start gap-3 relative z-10">
                      {/* Numbered circle */}
                      <div className="h-10 w-10 shrink-0 rounded-full bg-[#2563eb] text-white flex items-center justify-center font-bold text-[16px] shadow-[0_0_0_5px_white]">
                        {idx + 1}
                      </div>
                      {/* Separator */}
                      <span className="text-slate-300 self-center font-light text-lg leading-none">:</span>
                      {/* Step icon */}
                      <div className="h-9 w-9 shrink-0 rounded-lg bg-slate-100 flex items-center justify-center">
                        <svg className="w-5 h-5 text-[#2563eb]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={step.icon} />
                        </svg>
                      </div>
                      <div className="pt-0.5">
                        <p className="font-bold text-[15px] text-[#021737] mb-0.5">{t(step.key)}</p>
                        <p className="text-[13px] text-slate-500 leading-snug ltr:pr-4 rtl:pl-4">{t(step.keyD)}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Top Categories grid */}
            <div id="categories">
              <div className="flex items-center justify-between mb-8">
                <h3 className="text-xl md:text-2xl font-bold text-[#021737]">{t('cat.title')}</h3>
                <button
                  aria-label={t('cat.viewAll')}
                  onClick={() => navigate('/parts')}
                  className="text-[#2563eb] hover:text-[#1d4ed8] flex items-center gap-1 text-[13px] font-semibold transition-colors"
                >
                  {t('cat.viewAll')} <span aria-hidden="true" className="rtl:rotate-180">&rarr;</span>
                </button>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
                {categories.map(([nameKey, count, image, isMore]) => (
                  <article key={nameKey} className="group cursor-pointer" onClick={() => navigate('/parts')}>
                    <div className="h-full rounded-xl border border-slate-100 bg-white p-3 shadow hover:shadow-lg transition-all duration-300 hover:border-[#2563eb]/40 hover:-translate-y-0.5 flex flex-col items-center justify-center text-center">
                      <div className="w-full h-[90px] mb-2.5 flex items-center justify-center rounded-lg bg-slate-50 group-hover:bg-blue-50/50 transition-colors">
                        {isMore ? (
                          <div className="w-12 h-12 rounded-full bg-slate-100 flex items-center justify-center group-hover:bg-[#2563eb]/10 transition-colors">
                            <svg className="w-6 h-6 text-slate-400 group-hover:text-[#2563eb]" fill="currentColor" viewBox="0 0 24 24">
                              <path d="M6 10c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm12 0c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm-6 0c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z" />
                            </svg>
                          </div>
                        ) : (
                          <img
                            loading="lazy"
                            src={image}
                            alt={t(nameKey)}
                            className="max-h-[78px] max-w-[85%] object-contain drop-shadow-[0_6px_14px_rgba(0,0,0,0.18)] group-hover:scale-105 transition-transform duration-300"
                          />
                        )}
                      </div>
                      <p className="font-bold text-[13px] text-[#021737] leading-tight group-hover:text-[#2563eb] transition-colors">{t(nameKey)}</p>
                      <p className="text-[11px] text-slate-500 mt-0.5">{isMore ? t('cat.browseAll') : `${count} ${t('cat.parts')}`}</p>
                    </div>
                  </article>
                ))}
              </div>
            </div>
          </div>

          {/* Bottom row: AI CTA + feature cards */}
          <div className="mt-12 flex flex-col lg:flex-row gap-4">
            <div className="lg:w-[35%] rounded-2xl p-6 bg-[#021737] text-white flex flex-col justify-center relative overflow-hidden border border-white/10 shadow-lg">
              <div className="absolute inset-0 bg-gradient-to-br from-[#021737] to-[#0f1f35] opacity-80"></div>
              <div className="relative z-10 flex gap-4">
                <div className="w-16 h-16 shrink-0 bg-gradient-to-br from-blue-400/20 to-blue-600/20 rounded-full flex items-center justify-center border border-blue-400/30">
                  <BotIcon className="w-9 h-9" />
                </div>
                <div>
                  <p className="text-lg font-bold">{t('ai.title')}</p>
                  <p className="mt-1 text-[13px] text-slate-300 leading-relaxed">{t('ai.desc')}</p>
                  <button
                    aria-label={t('ai.try')}
                    onClick={() => navigate('/chat')}
                    className="mt-4 inline-flex items-center gap-2 rounded-lg bg-[#2563eb] hover:bg-[#1d4ed8] transition-colors px-4 py-2.5 text-[13px] font-semibold text-white border border-blue-500/50 shadow-md hover:shadow-lg"
                  >
                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z"/>
                    </svg>
                    {t('ai.try')}
                  </button>
                </div>
              </div>
            </div>

            <div className="lg:w-[65%] grid sm:grid-cols-2 md:grid-cols-4 gap-3">
              {featureCards.map(([titleKey, textKey, iconPath]) => (
                <div key={titleKey} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm flex flex-col hover:shadow-md transition-all">
                  <div className="w-10 h-10 rounded-full flex items-center justify-center mb-3 bg-blue-50">
                    <svg className="w-5 h-5 text-[#2563eb]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={iconPath} />
                    </svg>
                  </div>
                  <p className="font-bold text-[13px] text-[#021737] leading-tight">{t(titleKey)}</p>
                  <p className="text-[11px] text-slate-500 mt-1">{t(textKey)}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          ABOUT SECTION
      ═══════════════════════════════════════════════════════════════ */}
      <section id="about" className="bg-[#021737] text-white py-12">
        <div className="max-w-7xl mx-auto px-4">
          <div className="grid md:grid-cols-3 gap-8">
            <div>
              <img
                src="/logo-tests/autosparefinder-logo-header.svg"
                alt="AutoSpareFinder"
                className="h-16 w-auto object-contain opacity-90 mb-4"
              />
              <p className="text-[13px] text-slate-400 leading-relaxed max-w-xs">
                {t('footer.tagline')}
              </p>
            </div>
            <div>
              <h4 className="font-bold text-[14px] mb-4 text-slate-200">{t('footer.quickLinks')}</h4>
              <ul className="space-y-2 text-[13px] text-slate-400">
                <li><a href="/parts" className="hover:text-white transition-colors">{t('footer.browse')}</a></li>
                <li><a href="/chat" className="hover:text-white transition-colors">{t('footer.aiAssistant')}</a></li>
                <li><a href="/orders" className="hover:text-white transition-colors">{t('footer.track')}</a></li>
                <li><a href="/register" className="hover:text-white transition-colors">{t('footer.createAccount')}</a></li>
                <li><a href="/developers" className="hover:text-white transition-colors">{t('footer.developers')}</a></li>
              </ul>
            </div>
            <div>
              <h4 className="font-bold text-[14px] mb-4 text-slate-200">{t('footer.support')}</h4>
              <ul className="space-y-2 text-[13px] text-slate-400">
                <li><a href="https://wa.me/972532426920" target="_blank" rel="noreferrer" className="hover:text-white transition-colors flex items-center gap-1.5"><WhatsAppIcon className="w-3.5 h-3.5" /> {t('footer.waSupport')}</a></li>
                <li><a href="/chat" className="hover:text-white transition-colors">{t('footer.liveChat')}</a></li>
                <li><a href="/privacy" className="hover:text-white transition-colors">{t('footer.privacy')}</a></li>
                <li><a href="/terms" className="hover:text-white transition-colors">{t('footer.terms')}</a></li>
                <li><a href="/refund" className="hover:text-white transition-colors">{t('footer.refunds')}</a></li>
              </ul>
            </div>
          </div>
          <div className="mt-10 pt-6 border-t border-white/10 flex flex-col sm:flex-row items-center justify-between gap-3 text-[12px] text-slate-500">
            <span>{t('footer.rights', { year: new Date().getFullYear() })}</span>
            <div className="flex items-center gap-5">
              <a href="/developers" className="hover:text-slate-300 transition-colors">{t('footer.api')}</a>
              <a href="/privacy" className="hover:text-slate-300 transition-colors">{t('footer.privacyShort')}</a>
              <a href="/terms" className="hover:text-slate-300 transition-colors">{t('footer.termsShort')}</a>
              <a href="/refund" className="hover:text-slate-300 transition-colors">{t('footer.refundsShort')}</a>
            </div>
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════
          FLOATING WHATSAPP BUTTON
      ═══════════════════════════════════════════════════════════════ */}
      <a
        href="https://wa.me/972532426920"
        target="_blank"
        rel="noreferrer"
        aria-label="Chat on WhatsApp"
        className="fixed bottom-6 ltr:right-6 rtl:left-6 z-50 w-14 h-14 bg-[#25D366] rounded-full flex items-center justify-center shadow-[0_4px_20px_rgba(37,211,102,0.45)] hover:shadow-[0_6px_28px_rgba(37,211,102,0.6)] hover:scale-110 transition-all duration-200"
      >
        <WhatsAppIcon className="w-7 h-7" />
      </a>

    </div>
  )
}
