import { useState, useEffect, useRef } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import { useCartStore } from '../stores/cartStore'
import api from '../api/client'
import ConsentModal from './ConsentModal'
import BrandLogo from './BrandLogo'
import {
  MessageSquare, Search, ShoppingCart, Package, User,
  Settings, LogOut, Menu, X, Bell, Car, ChevronDown,
  LayoutDashboard, Bot,
} from 'lucide-react'
import toast from 'react-hot-toast'

const NAV_ITEMS = [
  { path: '/chat',    icon: MessageSquare, label: 'צ׳אט AI'     },
  { path: '/parts',   icon: Search,        label: 'חיפוש חלקים' },
  { path: '/orders',  icon: Package,       label: 'הזמנות'       },
  { path: '/account', icon: User,          label: 'האזור האישי' },
]

const TOP_NAV_ITEMS = NAV_ITEMS.filter((item) => item.path !== '/account')

export default function Layout({ children }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [unreadCount, setUnreadCount] = useState(0)
  const [notifications, setNotifications] = useState([])
  const [showNotifs, setShowNotifs] = useState(false)
  const [expandedNotifId, setExpandedNotifId] = useState(null)
  const [selectedNotif, setSelectedNotif] = useState(null)
  const notifsRef = useRef(null)
  const { user, logout, fetchMe } = useAuthStore()
  const { totals, setItems } = useCartStore()
  const location = useLocation()
  const navigate = useNavigate()
  const isAdminRoute = location.pathname === '/admin'
  const cartTotals = (() => { try { return totals() } catch { return { subtotal: 0, vat: 0, shipping: 0, total: 0, count: 0 } } })()
  const cartBadgeLabel = cartTotals.count > 99 ? '99+' : String(cartTotals.count)

  const mapServerCartItems = (serverItems = []) =>
    serverItems.map((item) => ({
      partId: item.partId,
      supplierPartId: item.supplierPartId || item.supplier_part_id || item.id,
      serverCartItemId: item.id,
      name: item.name,
      manufacturer: item.supplierName || 'Supplier',
      price: Number(item.price || 0),
      vat: 0,
      quantity: Number(item.quantity || 1),
    }))

  // Refresh user on mount to ensure is_admin and other fields are current
  useEffect(() => { fetchMe() }, [])

  const fetchNotifications = () =>
    api.get('/notifications').then(({ data }) => setNotifications(data.notifications || [])).catch(() => {})

  useEffect(() => {
    const fetchUnread = () =>
      api.get('/notifications/unread-count')
        .then(({ data }) => setUnreadCount(data.unread_count || 0))
        .catch(() => {})
    fetchUnread()
    const id = setInterval(fetchUnread, 30000)
    const handleClick = (e) => {
      if (notifsRef.current && !notifsRef.current.contains(e.target)) setShowNotifs(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => { clearInterval(id); document.removeEventListener('mousedown', handleClick) }
  }, [])

  useEffect(() => {
    if (!user) {
      setItems([])
      return
    }

    const syncCartBadge = () =>
      api.get('/customers/cart')
        .then(({ data }) => setItems(mapServerCartItems(data.items || [])))
        .catch(() => {})

    syncCartBadge()
    const id = setInterval(syncCartBadge, 30000)
    return () => clearInterval(id)
  }, [user?.id])

  const handleLogout = () => {
    logout()
    toast.success('התנתקת בהצלחה')
    navigate('/login')
  }

  const navigateFromNotification = (n) => {
    const d = n?.data || {}
    if (n?.type === 'order_update' || d.order_id) navigate('/orders')
    else if (n?.type === 'payment') navigate('/orders')
    else if (n?.type === 'threshold_alert' || n?.type === 'system_alert') navigate('/agents')
    else if (n?.type === 'message') navigate('/chat')
    else if (n?.type === 'marketing') navigate('/parts')
    else navigate('/chat')
  }

  return (
    <>
      {/* Consent modal — shown on first login until user accepts privacy policy + terms */}
      {user && !user.terms_accepted_at && <ConsentModal />}

      <div className="min-h-screen flex flex-col bg-brand-surface">
      {/* Top navbar */}
      <header className="fixed top-0 right-0 left-0 z-40 bg-[#1B2228] border-b border-slate-600 shadow-[0_10px_28px_rgba(0,0,0,0.35)]">
        <div className="flex items-center justify-between h-24 px-4 max-w-7xl mx-auto">
          {/* Logo - Root fix 1:1 image implementation with breakpoint support */}
          <Link to="/chat" className="flex items-center">
              <BrandLogo size="appHeader" alt="AutoSpare logo" blend />
            </Link>

          {/* Desktop nav */}
          <nav className="hidden md:flex items-center gap-1">
            {TOP_NAV_ITEMS.map(({ path, icon: Icon, label }) => (
              <Link
                key={path}
                to={path}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors
                  ${location.pathname === path
                    ? 'bg-[#00CCFF]/18 text-white border border-[#00CCFF]/60'
                    : 'text-slate-100 hover:bg-white/10 hover:text-white'
                  }`}
              >
                <Icon className="w-4 h-4" />
                {label}
              </Link>
            ))}
            {user?.is_admin && (
              <>
                <Link
                  to="/admin"
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors
                    ${(location.pathname === '/admin' || location.pathname.startsWith('/admin/'))
                      ? 'bg-[#00CCFF]/18 text-white border border-[#00CCFF]/60'
                      : 'text-slate-100 hover:bg-white/10'
                    }`}
                >
                  <LayoutDashboard className="w-4 h-4" />
                  ניהול
                </Link>
                <Link
                  to="/agents"
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors
                    ${location.pathname === '/agents'
                      ? 'bg-[#00CCFF]/18 text-white border border-[#00CCFF]/60'
                      : 'text-slate-100 hover:bg-white/10'
                    }`}
                >
                  <Bot className="w-4 h-4" />
                  סוכני AI
                </Link>
              </>
            )}
          </nav>

          {/* Right actions */}
          <div className="flex items-center gap-2">
            {/* Cart */}
            <Link to="/cart" className="relative p-2 rounded-lg text-slate-100 hover:bg-white/10 transition-colors">
              <ShoppingCart className="w-5 h-5" />
              {cartTotals.count > 0 && (
                <span className="absolute -top-1 -left-1 min-w-[1.25rem] h-5 px-1 bg-[#00CCFF] text-[#1B2228] text-[10px] rounded-full flex items-center justify-center font-black leading-none">
                  {cartBadgeLabel}
                </span>
              )}
            </Link>

            {/* Notifications bell */}
            <div className="relative" ref={notifsRef}>
              <button
                className="relative p-2 rounded-lg text-slate-100 hover:bg-white/10 transition-colors"
                onClick={() => { setShowNotifs(!showNotifs); if (!showNotifs) fetchNotifications() }}
              >
                <Bell className="w-5 h-5" />
                {unreadCount > 0 && (
                  <span className="absolute -top-1 -left-1 w-5 h-5 bg-red-500 text-white text-xs rounded-full flex items-center justify-center font-bold">
                    {unreadCount > 9 ? '9+' : unreadCount}
                  </span>
                )}
              </button>
              {showNotifs && (
                <div className="absolute left-0 top-full mt-1 w-96 max-w-[90vw] bg-white rounded-xl border border-gray-200 shadow-xl z-50">
                  <div className="flex items-center justify-between p-4 border-b border-gray-100">
                    <span className="font-bold text-brand-navy text-sm">התראות</span>
                    {unreadCount > 0 && (
                      <button
                        onClick={() => api.put('/notifications/read-all').then(() => { setUnreadCount(0); fetchNotifications() }).catch(() => {})}
                        className="text-xs text-brand-600 hover:text-brand-700"
                      >
                        סמן הכל כנקרא
                      </button>
                    )}
                  </div>
                  <div className="max-h-80 overflow-y-auto divide-y divide-gray-50">
                    {notifications.length === 0 ? (
                      <p className="text-sm text-gray-400 text-center py-8">אין התראות</p>
                    ) : notifications.map((n) => (
                      <div
                        key={n.id}
                        className={`p-4 hover:bg-gray-50 cursor-pointer ${!n.read_at ? 'bg-brand-50/70' : ''}`}
                        onClick={() => {
                          if (!n.read_at) {
                            api.put(`/notifications/${n.id}/read`)
                              .then(() => {
                                setUnreadCount((c) => Math.max(0, c - 1))
                                fetchNotifications()
                              })
                              .catch(() => {})
                          }
                          setSelectedNotif(n)
                        }}
                      >
                        <p className={`text-sm ${!n.read_at ? 'font-semibold text-brand-navy' : 'text-gray-700'}`}>{n.title}</p>
                        {n.message && (
                          <div className="mt-0.5">
                            <p
                              className={`text-xs text-gray-500 whitespace-pre-wrap break-words ${expandedNotifId === n.id ? '' : 'line-clamp-2'}`}
                            >
                              {n.message}
                            </p>
                            {n.message.length > 120 && (
                              <button
                                type="button"
                                className="mt-1 text-[11px] text-brand-600 hover:text-brand-700"
                                onClick={(e) => {
                                  e.stopPropagation()
                                  setExpandedNotifId((prev) => (prev === n.id ? null : n.id))
                                }}
                              >
                                {expandedNotifId === n.id ? 'הצג פחות' : 'הצג עוד'}
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* User menu */}
            <div className="relative group">
              <button className="flex items-center gap-2 px-3 py-2 rounded-lg text-slate-100 hover:bg-white/10 transition-colors">
                <div className="w-7 h-7 bg-white/10 border border-[#00CCFF]/50 rounded-full flex items-center justify-center">
                  <span className="text-[#9AEFFF] font-black text-xs">
                    {user?.full_name?.charAt(0) || 'U'}
                  </span>
                </div>
                <span className="hidden md:block text-sm font-medium text-slate-100 max-w-24 truncate">
                  {user?.full_name}
                </span>
                <ChevronDown className="w-4 h-4 text-slate-300" />
              </button>
              <div className="absolute left-0 top-full mt-1 w-48 bg-white rounded-xl border border-gray-200 shadow-lg opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all duration-150 z-50">
                <div className="p-3 border-b border-gray-100">
                  <p className="text-sm font-semibold text-brand-navy truncate">{user?.full_name}</p>
                  <p className="text-xs text-gray-500 truncate">{user?.email}</p>
                </div>
                <div className="p-1">
                  <Link to="/profile" className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-gray-50 text-sm text-gray-700">
                    <User className="w-4 h-4" /> הפרופיל שלי
                  </Link>
                  <button
                    onClick={handleLogout}
                    className="w-full flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-red-50 text-sm text-red-600"
                  >
                    <LogOut className="w-4 h-4" /> התנתק
                  </button>
                </div>
              </div>
            </div>

            {/* Mobile menu btn */}
            {!isAdminRoute && (
              <button className="md:hidden p-2 rounded-lg text-slate-100 hover:bg-white/10 transition-colors" onClick={() => setSidebarOpen(!sidebarOpen)}>
                {sidebarOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
              </button>
            )}
          </div>
        </div>
      </header>

      {/* Mobile sidebar */}
      {sidebarOpen && !isAdminRoute && (
        <div className="fixed inset-0 z-30 md:hidden" onClick={() => setSidebarOpen(false)}>
          <div className="absolute inset-0 bg-black/40" />
          <nav
            className="absolute top-24 right-0 bottom-0 w-72 bg-white border-l border-gray-200 p-4 overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            {NAV_ITEMS.map(({ path, icon: Icon, label }) => (
              <Link
                key={path}
                to={path}
                onClick={() => setSidebarOpen(false)}
                className={`flex items-center gap-3 px-4 py-3 rounded-xl mb-1 text-sm font-medium
                  ${location.pathname === path ? 'bg-[#00CCFF]/20 text-[#1B2228] border border-[#00CCFF]/50' : 'text-gray-700 hover:bg-gray-50'}`}
              >
                <Icon className="w-5 h-5" /> {label}
              </Link>
            ))}
            <button onClick={handleLogout} className="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-medium text-red-600 hover:bg-red-50 mt-2">
              <LogOut className="w-5 h-5" /> התנתק
            </button>
          </nav>
        </div>
      )}

      {/* Page content */}
      <main className="flex-1 pt-24">
        <div className="max-w-7xl mx-auto px-4 py-6">
          {children}
        </div>
      </main>

      {/* Footer */}
      <footer className="bg-white border-t border-brand-100 py-4 mt-auto">
        <div className="max-w-7xl mx-auto px-4 flex flex-col sm:flex-row items-center justify-between gap-2 text-xs text-gray-500" dir="rtl">
          <span>© {new Date().getFullYear()} AutoSpare</span>
          <div className="flex items-center gap-4">
            <Link to="/privacy" className="hover:text-brand-600 transition-colors">מדיניות פרטיות</Link>
            <Link to="/terms" className="hover:text-brand-600 transition-colors">תנאי שימוש</Link>
            <Link to="/refund" className="hover:text-brand-600 transition-colors">ביטולים והחזרות</Link>
          </div>
        </div>
      </footer>

      {selectedNotif && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-lg rounded-2xl bg-white shadow-2xl border border-gray-200">
            <div className="flex items-center justify-between p-4 border-b border-gray-100">
              <h3 className="text-sm font-bold text-brand-navy">פרטי התראה</h3>
              <button
                type="button"
                className="text-gray-400 hover:text-gray-600"
                onClick={() => setSelectedNotif(null)}
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-4 space-y-2">
              <p className="text-sm font-semibold text-brand-navy whitespace-pre-wrap break-words">{selectedNotif.title}</p>
              {!!selectedNotif.message && (
                <p className="text-sm text-gray-600 whitespace-pre-wrap break-words">{selectedNotif.message}</p>
              )}
            </div>
            <div className="p-4 border-t border-gray-100 flex items-center justify-end gap-2">
              <button
                type="button"
                className="btn-ghost px-4 py-2"
                onClick={() => setSelectedNotif(null)}
              >
                סגור
              </button>
              <button
                type="button"
                className="btn-primary px-4 py-2"
                onClick={() => {
                  const n = selectedNotif
                  setSelectedNotif(null)
                  setShowNotifs(false)
                  navigateFromNotification(n)
                }}
              >
                פתח עמוד קשור
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
    </>
  )
}
