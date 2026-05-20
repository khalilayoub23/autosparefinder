import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import api from '../api/client'
import { ordersApi } from '../api/orders'

const SEARCH_MODES = [
  { key: 'vin', label: 'VIN' },
  { key: 'oem', label: 'OEM' },
  { key: 'sku', label: 'SKU' },
  { key: 'vehicle', label: 'Vehicle' },
]

export default function LandingPage() {
  const navigate = useNavigate()
  const { user } = useAuthStore()
  const [mode, setMode] = useState('vin')
  const [query, setQuery] = useState('')
  const [metrics, setMetrics] = useState({ vehicles: 12, orders: 28, quotes: 9, recommendations: 16 })
  const [recentOrders, setRecentOrders] = useState([
    { number: 'ASF-101', status: 'Supplier Approved', progress: 70 },
    { number: 'ASF-102', status: 'Packaging', progress: 54 },
  ])

  useEffect(() => {
    let mounted = true
    if (!user || !localStorage.getItem('access_token')) return

    const run = async () => {
      try {
        const [profileRes, ordersRes] = await Promise.all([
          api.get('/profile'),
          ordersApi.getAll(20),
        ])

        if (!mounted) return

        const orders = Array.isArray(ordersRes?.data?.orders) ? ordersRes.data.orders : []
        const delivered = orders.filter((o) => o.status === 'delivered').length
        const pending = orders.filter((o) => o.status === 'pending_payment').length

        const estimateProgress = (status) => ({
          pending_payment: 20,
          paid: 40,
          supplier_ordered: 55,
          shipped: 80,
          delivered: 100,
        }[status] || 35)

        const mapped = orders.slice(0, 2).map((o) => ({
          number: o.order_number || String(o.id || '').slice(0, 8),
          status: o.status || 'processing',
          progress: estimateProgress(o.status),
        }))

        if (mapped.length > 0) setRecentOrders(mapped)

        setMetrics({
          vehicles: Number(profileRes?.data?.vehicles_count || profileRes?.data?.vehicles?.length || 0),
          orders: orders.length,
          quotes: pending,
          recommendations: Math.max(orders.length + delivered, 0),
        })
      } catch (_) {
        // Keep static fallback values on landing when API is unavailable
      }
    }

    run()
    return () => { mounted = false }
  }, [user?.id])

  const searchPlaceholder = useMemo(() => {
    switch (mode) {
      case 'vin': return 'הקלד VIN או מספר רישוי'
      case 'oem': return 'הקלד מספר OEM'
      case 'sku': return 'הקלד SKU או מק"ט'
      default: return 'הקלד יצרן, דגם ושנה'
    }
  }, [mode])

  const handleSearch = (e) => {
    e.preventDefault()
    const q = query.trim()
    const params = new URLSearchParams()
    if (q) params.set('search', q)
    params.set('mode', mode)
    navigate(`/parts?${params.toString()}`)
  }

  return (
    <div className="lp-root" dir="rtl" lang="he">
      <style>{`
        .lp-root * { box-sizing: border-box; }
        .lp-root {
          font-family: 'Heebo', 'Inter', 'Segoe UI', sans-serif;
          background: #0a1628;
          color: #fff;
          min-height: 100vh;
          overflow-x: hidden;
        }
        .lp-bg {
          position: fixed;
          inset: 0;
          background:
            radial-gradient(circle at top right, rgba(30,111,240,0.22), transparent 36%),
            radial-gradient(circle at bottom left, rgba(23,162,184,0.18), transparent 44%);
          z-index: 0;
        }
        .lp-container { width: min(1280px, 92%); margin: 0 auto; position: relative; z-index: 1; }
        .lp-header {
          position: sticky; top: 0; z-index: 20;
          background: rgba(10, 22, 40, 0.84);
          backdrop-filter: blur(14px);
          border-bottom: 1px solid rgba(255,255,255,.1);
        }
        .lp-nav { display: flex; align-items: center; justify-content: space-between; min-height: 72px; gap: 16px; }
        .lp-brand { display: flex; align-items: center; gap: 12px; }
        .lp-logo {
          width: 50px; height: 50px; border-radius: 14px;
          display: grid; place-items: center;
          background: linear-gradient(135deg, #1e6ff0, #17a2b8);
          box-shadow: 0 0 28px rgba(30,111,240,.35);
          font-size: 24px;
        }
        .lp-brand h1 { font-size: 1.28rem; margin: 0; font-weight: 800; }
        .lp-brand p { margin: 0; color: #9bb3d7; font-size: .76rem; }
        .lp-links { display: flex; gap: 20px; color: #c9d6ea; font-size: .95rem; }
        .lp-links a:hover { color: #fff; }
        .lp-actions { display: flex; gap: 10px; align-items: center; }
        .lp-btn {
          border: 1px solid rgba(255,255,255,.14);
          color: #fff; background: rgba(255,255,255,.05);
          border-radius: 12px; padding: 10px 16px; font-weight: 700; cursor: pointer;
        }
        .lp-btn-primary {
          border: none;
          background: linear-gradient(135deg, #1e6ff0, #17a2b8);
          box-shadow: 0 0 24px rgba(30,111,240,.3);
        }
        .lp-hero {
          display: grid; grid-template-columns: 1.05fr .95fr; align-items: center;
          gap: 52px; padding: 72px 0 96px;
        }
        .lp-badge {
          display: inline-flex; align-items: center; gap: 8px;
          padding: 10px 14px; border-radius: 999px;
          border: 1px solid rgba(30,111,240,.35);
          background: rgba(30,111,240,.14); color: #95d3ff;
          margin-bottom: 20px;
        }
        .lp-title {
          margin: 0 0 18px;
          font-size: clamp(2rem, 4.8vw, 4.4rem);
          line-height: 1.08;
          font-weight: 900;
        }
        .lp-title-accent {
          background: linear-gradient(90deg, #34a6ff, #72e3ff);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
        }
        .lp-sub {
          color: #c4d0e2;
          font-size: clamp(1rem, 1.6vw, 1.2rem);
          line-height: 1.9;
          margin-bottom: 26px;
        }
        .lp-search {
          background: rgba(255,255,255,.05);
          border: 1px solid rgba(255,255,255,.11);
          border-radius: 24px;
          padding: 18px;
          box-shadow: 0 0 34px rgba(30,111,240,.14);
        }
        .lp-tabs { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 14px; }
        .lp-tab {
          border: 1px solid rgba(255,255,255,.12);
          background: rgba(255,255,255,.04);
          border-radius: 12px;
          padding: 9px 14px;
          color: #e9f2ff;
          cursor: pointer;
          font-weight: 700;
        }
        .lp-tab.active {
          border-color: rgba(52,166,255,.65);
          background: rgba(30,111,240,.2);
        }
        .lp-search-row { display: flex; gap: 10px; }
        .lp-search-row input {
          flex: 1;
          border-radius: 14px;
          border: 1px solid rgba(255,255,255,.12);
          background: #0f2038;
          color: #fff;
          padding: 14px 16px;
          font-size: 15px;
        }
        .lp-search-row button {
          border: 0;
          border-radius: 14px;
          padding: 14px 22px;
          cursor: pointer;
          font-weight: 800;
          color: #fff;
          background: linear-gradient(135deg, #1e6ff0, #17a2b8);
        }
        .lp-trust { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 16px; }
        .lp-trust-card {
          background: rgba(255,255,255,.05);
          border: 1px solid rgba(255,255,255,.1);
          border-radius: 14px;
          text-align: center;
          padding: 12px;
          color: #d2def0;
          font-size: .9rem;
        }
        .lp-visual-card {
          position: relative;
          background: linear-gradient(135deg, #132542, #0c1a2f);
          border-radius: 30px;
          border: 1px solid rgba(255,255,255,.12);
          padding: 24px;
          box-shadow: 0 0 56px rgba(30,111,240,.22);
        }
        .lp-status { width: 12px; height: 12px; border-radius: 50%; background: #28a745; box-shadow: 0 0 16px #28a745; animation: lp-pulse 1.8s infinite; }
        @keyframes lp-pulse { 0% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.25); opacity: .7; } 100% { transform: scale(1); opacity: 1; } }
        .lp-car {
          margin-top: 16px;
          border-radius: 22px;
          border: 1px solid rgba(255,255,255,.1);
          background: radial-gradient(circle, rgba(30,111,240,.16), transparent 64%), #081523;
          min-height: 300px;
          display: grid;
          place-items: center;
          font-size: 90px;
        }
        .lp-section { padding: 0 0 80px; }
        .lp-section h3 { font-size: clamp(1.7rem, 3vw, 3rem); margin: 0 0 10px; font-weight: 900; }
        .lp-section p { color: #b4c4db; margin: 0 0 26px; }
        .lp-categories { display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px; }
        .lp-cat {
          background: rgba(255,255,255,.05);
          border: 1px solid rgba(255,255,255,.1);
          border-radius: 18px;
          padding: 18px;
          text-align: center;
          transition: .25s ease;
        }
        .lp-cat:hover { transform: translateY(-4px); border-color: rgba(52,166,255,.5); }
        .lp-cat .icon { font-size: 30px; margin-bottom: 10px; }
        .lp-dashboard {
          padding: 82px 0;
          background: rgba(255,255,255,.04);
          border-top: 1px solid rgba(255,255,255,.12);
        }
        .lp-kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }
        .lp-kpi { background: rgba(14,31,52,.85); border: 1px solid rgba(255,255,255,.1); border-radius: 16px; padding: 18px; }
        .lp-kpi p { margin: 0 0 8px; color: #9ab0cc; }
        .lp-kpi h4 { margin: 0; font-size: 2rem; }
        .lp-panels { display: grid; grid-template-columns: 2fr 1fr; gap: 14px; }
        .lp-panel { background: rgba(14,31,52,.85); border: 1px solid rgba(255,255,255,.1); border-radius: 18px; padding: 18px; }
        .lp-order { display: flex; justify-content: space-between; align-items: center; padding: 14px; border-radius: 12px; background: rgba(255,255,255,.05); border: 1px solid rgba(255,255,255,.1); margin-bottom: 10px; }
        .lp-actions-col { display: grid; gap: 10px; }
        .lp-actions-col a { display: block; background: rgba(255,255,255,.05); border: 1px solid rgba(255,255,255,.1); border-radius: 12px; padding: 12px; }

        @media (max-width: 1100px) {
          .lp-links { display: none; }
          .lp-hero { grid-template-columns: 1fr; }
          .lp-categories { grid-template-columns: repeat(3, 1fr); }
          .lp-panels { grid-template-columns: 1fr; }
        }
        @media (max-width: 760px) {
          .lp-nav { min-height: 68px; }
          .lp-actions { gap: 8px; }
          .lp-btn { padding: 9px 12px; font-size: .9rem; }
          .lp-search-row { flex-direction: column; }
          .lp-trust { grid-template-columns: repeat(2, 1fr); }
          .lp-categories { grid-template-columns: repeat(2, 1fr); }
          .lp-kpis { grid-template-columns: repeat(2, 1fr); }
        }
      `}</style>

      <div className="lp-bg" />

      <header className="lp-header">
        <div className="lp-container lp-nav">
          <div className="lp-brand">
            <div className="lp-logo">🔍</div>
            <div>
              <h1>AutoSpareFinder</h1>
              <p>AI Automotive Marketplace</p>
            </div>
          </div>

          <nav className="lp-links" aria-label="Main navigation">
            <a href="#top">דף הבית</a>
            <a href="#categories">קטגוריות</a>
            <a href="#dashboard">בקשת הצעת מחיר</a>
            <a href="#dashboard">תמיכה</a>
            <Link to={user ? '/account' : '/login'}>האזור האישי</Link>
            {user?.is_admin && <Link to="/admin">לוח ניהול</Link>}
          </nav>

          <div className="lp-actions">
            {user ? (
              <>
                <Link className="lp-btn lp-btn-primary" to="/chat">כניסה למערכת</Link>
                {user.is_admin && <Link className="lp-btn" to="/admin">ניהול</Link>}
              </>
            ) : (
              <>
                <Link className="lp-btn" to="/login">התחברות</Link>
                <Link className="lp-btn lp-btn-primary" to="/register">הרשמה</Link>
              </>
            )}
          </div>
        </div>
      </header>

      <main className="lp-container" id="top">
        <section className="lp-hero">
          <div>
            <div className="lp-badge">🤖 AI Assistant Active</div>
            <h2 className="lp-title">
              לא רק מחפשים חלק <span className="lp-title-accent">מוצאים פתרון</span>
            </h2>
            <p className="lp-sub">
              פלטפורמת AI מתקדמת למציאת חלקי חילוף לפי VIN, OEM, SKU או פרטי רכב עם השוואת ספקים,
              הצעות מחיר ותמיכה אנושית.
            </p>

            <form className="lp-search" onSubmit={handleSearch}>
              <div className="lp-tabs" role="tablist" aria-label="Search mode">
                {SEARCH_MODES.map((s) => (
                  <button
                    key={s.key}
                    type="button"
                    className={`lp-tab ${mode === s.key ? 'active' : ''}`}
                    onClick={() => setMode(s.key)}
                    role="tab"
                    aria-selected={mode === s.key}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
              <div className="lp-search-row">
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={searchPlaceholder}
                  aria-label="Search input"
                />
                <button type="submit">חפש חלק</button>
              </div>

              <div className="lp-trust">
                <div className="lp-trust-card">AI Part Matching</div>
                <div className="lp-trust-card">ספקים מאומתים</div>
                <div className="lp-trust-card">החזרות תוך 14 יום</div>
                <div className="lp-trust-card">תמיכה אנושית</div>
              </div>
            </form>
          </div>

          <div className="lp-visual-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <h3 style={{ margin: 0, fontSize: '1.7rem' }}>AI Vehicle Scan</h3>
                <p style={{ margin: '6px 0 0', color: '#9db4d4' }}>Realtime Compatibility Analysis</p>
              </div>
              <div className="lp-status" />
            </div>
            <div className="lp-car" aria-hidden="true">🚘</div>
          </div>
        </section>

        <section className="lp-section" id="categories">
          <h3>קטגוריות מובילות</h3>
          <p>מצא במהירות את החלק שאתה צריך</p>
          <div className="lp-categories">
            {['מנוע', 'בלמים', 'חשמל', 'גיר', 'מתלים', 'קירור'].map((name) => (
              <Link key={name} to="/parts" className="lp-cat">
                <div className="icon">⚙️</div>
                <h4 style={{ margin: 0 }}>{name}</h4>
              </Link>
            ))}
          </div>
        </section>
      </main>

      <section className="lp-dashboard" id="dashboard">
        <div className="lp-container">
          <div className="lp-section" style={{ paddingBottom: 22 }}>
            <h3>האזור האישי</h3>
            <p>ניהול הזמנות, רכבים שמורים והצעות מחיר במקום אחד</p>
          </div>

          <div className="lp-kpis">
            <div className="lp-kpi"><p>Saved Vehicles</p><h4>{metrics.vehicles}</h4></div>
            <div className="lp-kpi"><p>Orders</p><h4>{metrics.orders}</h4></div>
            <div className="lp-kpi"><p>Quotes</p><h4>{metrics.quotes}</h4></div>
            <div className="lp-kpi"><p>AI Recommendations</p><h4>{metrics.recommendations}</h4></div>
          </div>

          <div className="lp-panels">
            <div className="lp-panel">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <div>
                  <h3 style={{ margin: 0, fontSize: '1.2rem' }}>Realtime Orders</h3>
                  <p style={{ margin: '6px 0 0', color: '#9ab0cc' }}>מעקב בזמן אמת אחרי הזמנות</p>
                </div>
                <span style={{ background: 'rgba(40,167,69,.18)', color: '#92ffbe', borderRadius: 10, padding: '8px 12px' }}>Live</span>
              </div>

              {recentOrders.map((order) => (
                <div className="lp-order" key={order.number}>
                  <div>
                    <h4 style={{ margin: 0 }}>Order #{order.number}</h4>
                    <p style={{ margin: '4px 0 0', color: '#9ab0cc' }}>{order.status}</p>
                  </div>
                  <div style={{ width: 150, height: 8, background: 'rgba(255,255,255,.1)', borderRadius: 999, overflow: 'hidden' }}>
                    <span style={{ display: 'block', width: `${order.progress}%`, height: '100%', background: 'linear-gradient(90deg,#1e6ff0,#17a2b8)' }} />
                  </div>
                </div>
              ))}
            </div>

            <div className="lp-panel">
              <h3 style={{ marginTop: 0, fontSize: '1.2rem' }}>Quick Actions</h3>
              <div className="lp-actions-col">
                <Link to="/parts">בקשת הצעת מחיר</Link>
                <Link to="/parts">העלאת VIN</Link>
                <Link to="/chat">פתיחת פנייה</Link>
                <a href="https://wa.me/972000000000" target="_blank" rel="noreferrer">WhatsApp Support</a>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
