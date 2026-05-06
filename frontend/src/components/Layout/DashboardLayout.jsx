import React, { useMemo, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Menu, X } from 'lucide-react'
import { useAuthStore } from '../../stores/authStore'
import BrandLogo from '../BrandLogo'

const NAV_ITEMS = [
  { label: 'לוח בקרה', to: '/admin' },
  { label: 'ניהול מלאי', to: '/inventory' },
  { label: 'הזמנות', to: '/admin/orders' },
  { label: 'סוכנים', to: '/agents' },
]

function isActivePath(currentPath, to) {
  if (to === '/admin') return currentPath === '/admin'
  return currentPath === to || currentPath.startsWith(`${to}/`)
}

const SidebarLink = ({ label, to, onClick }) => {
  const location = useLocation()
  const active = isActivePath(location.pathname, to)

  return (
    <Link
      to={to}
      onClick={onClick}
      className={`block px-4 py-3 mb-1 rounded-lg transition-colors font-medium text-sm border ${
        active
          ? 'bg-[#00CCFF]/15 border-[#00CCFF]/60 text-white'
          : 'border-transparent text-slate-200 hover:bg-white/10 hover:text-white'
      }`}
    >
      {label}
    </Link>
  )
}

const DashboardLayout = ({ children }) => {
  const [mobileOpen, setMobileOpen] = useState(false)
  const { user } = useAuthStore()

  const initials = useMemo(() => {
    const fullName = String(user?.full_name || '').trim()
    if (fullName) {
      const parts = fullName.split(/\s+/).filter(Boolean)
      const a = parts[0]?.[0] || ''
      const b = parts[1]?.[0] || ''
      return `${a}${b}`.toUpperCase() || 'AD'
    }
    const email = String(user?.email || '').trim()
    return (email.slice(0, 2) || 'AD').toUpperCase()
  }, [user?.full_name, user?.email])

  const closeMobile = () => setMobileOpen(false)

  return (
    <div className="min-h-screen bg-brand-surface font-sans" dir="rtl">
      <header className="lg:hidden sticky top-0 z-40 h-20 bg-[#1B2228] border-b border-slate-600 px-4 flex items-center justify-between shadow-[0_8px_24px_rgba(0,0,0,0.32)]">
        <button
          type="button"
          onClick={() => setMobileOpen((v) => !v)}
          className="w-10 h-10 rounded-lg border border-slate-500 flex items-center justify-center text-slate-100"
          aria-label="Toggle navigation"
        >
          {mobileOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>

        <Link to="/admin" className="flex items-center justify-end min-w-0">
          <BrandLogo size="dashboard" alt="AutoSpare logo" priority blend />
        </Link>
      </header>

      <div className="flex min-h-[calc(100vh-5rem)] lg:min-h-screen">
        <div
          className={`fixed inset-0 z-40 bg-slate-900/50 transition-opacity lg:hidden ${
            mobileOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'
          }`}
          onClick={closeMobile}
        />

        <aside
          className={`fixed top-0 right-0 z-50 h-full w-72 bg-[#1B2228] p-6 flex flex-col transition-transform duration-200 lg:static lg:translate-x-0 lg:h-auto ${
            mobileOpen ? 'translate-x-0' : 'translate-x-full'
          }`}
        >
          <div className="mb-8 px-2 flex items-center justify-between lg:justify-center">
            <Link to="/admin" onClick={closeMobile} className="flex items-center justify-center">
              <BrandLogo size="dashboard" alt="AutoSpare logo" blend />
            </Link>
            <button
              type="button"
              onClick={closeMobile}
              className="lg:hidden w-9 h-9 rounded-lg border border-slate-600 text-slate-200 flex items-center justify-center"
              aria-label="Close navigation"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          <nav className="flex-1 overflow-y-auto pr-1">
            {NAV_ITEMS.map((item) => (
              <SidebarLink key={item.to} label={item.label} to={item.to} onClick={closeMobile} />
            ))}
          </nav>

          <div className="mt-auto pt-6 border-t border-slate-700">
            <SidebarLink label="חזרה למערכת" to="/" onClick={closeMobile} />
          </div>
        </aside>

        <main className="flex-1 min-w-0 flex flex-col">
          <header className="hidden lg:flex h-20 bg-[#1B2228] border-b border-slate-600 items-center justify-between px-6 xl:px-8 shadow-[0_8px_24px_rgba(0,0,0,0.28)]">
            <div className="text-slate-300 text-sm">מערכת פעילה: שרת ייצור</div>
            <div className="w-9 h-9 rounded-full bg-[#00CCFF]/20 border border-[#00CCFF]/70 flex items-center justify-center text-[#E2F8FF] text-xs font-black">
              {initials}
            </div>
          </header>

          <section className="p-4 sm:p-6 xl:p-8 overflow-x-hidden w-full">
            {children}
          </section>
        </main>
      </div>
    </div>
  )
}

export default DashboardLayout
