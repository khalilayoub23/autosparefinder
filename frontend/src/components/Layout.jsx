import { useState, useEffect, useRef } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'
import { useCartStore } from '../stores/cartStore'
import api from '../api/client'
import {
  MessageSquare, Search, ShoppingCart, Package, User,
  Settings, LogOut, Menu, X, Bell, Car, ChevronDown,
  LayoutDashboard, Wrench,
} from 'lucide-react'
import toast from 'react-hot-toast'

const NAV_ITEMS = [
  { path: '/',        icon: MessageSquare, label: 'צ׳אט AI'     },
  { path: '/parts',   icon: Search,        label: 'חיפוש חלקים' },
  { path: '/orders',  icon: Package,       label: 'הזמנות'       },
  { path: '/profile', icon: User,          label: 'פרופיל'       },
]

export default function Layout({ children }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [unreadCount, setUnreadCount] = useState(0)
  const [notifications, setNotifications] = useState([])
  const [showNotifs, setShowNotifs] = useState(false)
  const notifsRef = useRef(null)
  const { user, logout } = useAuthStore()
  const { totals } = useCartStore()
  const location = useLocation()
  const navigate = useNavigate()
  const cartTotals = totals()

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

  const handleLogout = () => {
    logout()
    toast.success('התנתקת בהצלחה')
    navigate('/login')
  }

  return (
    <div className="min-h-screen flex flex-col bg-gray-50">
      {/* Top navbar */}
      <header className="fixed top-0 right-0 left-0 z-40 bg-white border-b border-gray-200 shadow-sm">
        <div className="flex items-center justify-between h-16 px-4 max-w-7xl mx-auto">
          {/* Logo */}
          <Link to="/" className="flex items-center gap-2">
            <div className="w-9 h-9 bg-brand-600 rounded-xl flex items-center justify-center">
              <Wrench className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold text-gray-900">Auto <span className="text-brand-600">Spare</span></span>
          </Link>

          {/* Desktop nav */}
          <nav className="hidden md:flex items-center gap-1">
            {NAV_ITEMS.map(({ path, icon: Icon, label }) => (
              <Link
                key={path}
                to={path}
                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors
                  ${location.pathname === path
                    ? 'bg-brand-50 text-brand-700'
                    : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
                  }`}
              >
                <Icon className="w-4 h-4" />
                {label}
              </Link>
            ))}
            {user?.is_admin && (
              <Link
                to="/admin"
                className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors
                  ${location.pathname.startsWith('/admin')
                    ? 'bg-purple-50 text-purple-700'
                    : 'text-gray-600 hover:bg-gray-100'
                  }`}
              >
                <LayoutDashboard className="w-4 h-4" />
                ניהול
              </Link>
            )}
          </nav>

          {/* Right actions */}
          <div className="flex items-center gap-2">
            {/* Cart */}
            <Link to="/cart" className="relative btn-ghost p-2">
              <ShoppingCart className="w-5 h-5" />
              {cartTotals.count > 0 && (
                <span className="absolute -top-1 -left-1 w-5 h-5 bg-brand-600 text-white text-xs rounded-full flex items-center justify-center font-bold">
                  {cartTotals.count}
                </span>
              )}
            </Link>

            {/* Notifications bell */}
            <div className="relative" ref={notifsRef}>
              <button
                className="relative btn-ghost p-2"
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
                <div className="absolute left-0 top-full mt-1 w-80 bg-white rounded-xl border border-gray-200 shadow-xl z-50">
                  <div className="flex items-center justify-between p-4 border-b border-gray-100">
                    <span className="font-bold text-gray-900 text-sm">התראות</span>
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
                        className={`p-4 hover:bg-gray-50 cursor-pointer ${!n.read_at ? 'bg-blue-50/40' : ''}`}
                        onClick={() => {
                          if (!n.read_at) api.put(`/notifications/${n.id}/read`).then(() => { setUnreadCount((c) => Math.max(0, c - 1)); fetchNotifications() }).catch(() =>{})
                        }}
                      >
                        <p className={`text-sm ${!n.read_at ? 'font-semibold text-gray-900' : 'text-gray-700'}`}>{n.title}</p>
                        {n.message && <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{n.message}</p>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* User menu */}
            <div className="relative group">
              <button className="flex items-center gap-2 btn-ghost px-3 py-2">
                <div className="w-7 h-7 bg-brand-100 rounded-full flex items-center justify-center">
                  <span className="text-brand-700 font-bold text-xs">
                    {user?.full_name?.charAt(0) || 'U'}
                  </span>
                </div>
                <span className="hidden md:block text-sm font-medium text-gray-700 max-w-24 truncate">
                  {user?.full_name}
                </span>
                <ChevronDown className="w-4 h-4 text-gray-400" />
              </button>
              <div className="absolute left-0 top-full mt-1 w-48 bg-white rounded-xl border border-gray-200 shadow-lg opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all duration-150 z-50">
                <div className="p-3 border-b border-gray-100">
                  <p className="text-sm font-semibold text-gray-900 truncate">{user?.full_name}</p>
                  <p className="text-xs text-gray-500 truncate">{user?.email}</p>
                </div>
                <div className="p-1">
                  <Link to="/profile" className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-gray-50 text-sm text-gray-700">
                    <User className="w-4 h-4" /> הפרופיל שלי
                  </Link>
                  {user?.is_admin && (
                    <Link to="/admin" className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-gray-50 text-sm text-gray-700">
                      <LayoutDashboard className="w-4 h-4" /> לוח ניהול
                    </Link>
                  )}
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
            <button className="md:hidden btn-ghost p-2" onClick={() => setSidebarOpen(!sidebarOpen)}>
              {sidebarOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
            </button>
          </div>
        </div>
      </header>

      {/* Mobile sidebar */}
      {sidebarOpen && (
        <div className="fixed inset-0 z-30 md:hidden" onClick={() => setSidebarOpen(false)}>
          <div className="absolute inset-0 bg-black/40" />
          <nav
            className="absolute top-16 right-0 bottom-0 w-72 bg-white border-l border-gray-200 p-4 overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            {NAV_ITEMS.map(({ path, icon: Icon, label }) => (
              <Link
                key={path}
                to={path}
                onClick={() => setSidebarOpen(false)}
                className={`flex items-center gap-3 px-4 py-3 rounded-xl mb-1 text-sm font-medium
                  ${location.pathname === path ? 'bg-brand-50 text-brand-700' : 'text-gray-700 hover:bg-gray-50'}`}
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
      <main className="flex-1 pt-16">
        <div className="max-w-7xl mx-auto px-4 py-6">
          {children}
        </div>
      </main>
    </div>
  )
}
