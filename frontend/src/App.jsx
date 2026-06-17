import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Component } from 'react'
import { Toaster } from 'react-hot-toast'
import Layout from './components/Layout'
import ProtectedRoute from './components/ProtectedRoute'
import Login from './pages/Login'
import Register from './pages/Register'
import ResetPassword from './pages/ResetPassword'
import Privacy from './pages/Privacy'
import Terms from './pages/Terms'
import Refund from './pages/Refund'
import Chat from './pages/Chat'
import LandingPage from './pages/LandingPage'
import ClientPortal from './pages/ClientPortal'
import Parts from './pages/Parts'
import Orders from './pages/Orders'
import Cart from './pages/Cart'
import Profile from './pages/Profile'
import Admin from './pages/Admin'
import Agents from './pages/Agents'
import PaymentSuccess from './pages/PaymentSuccess'

class ErrorBoundary extends Component {
  state = { error: null, info: null }
  static getDerivedStateFromError(error) { return { error } }
  componentDidCatch(error, info) { this.setState({ info }) }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: '2rem', fontFamily: 'sans-serif' }}>
          <h2 style={{ textAlign: 'center' }}>משהו השתבש</h2>
          <p style={{ color: '#666', fontSize: '0.9rem', textAlign: 'center' }}>{this.state.error?.message}</p>
          <pre style={{ background: '#f4f4f4', padding: '1rem', fontSize: '0.75rem', overflowX: 'auto', marginTop: '1rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '300px', overflowY: 'auto' }}>
            {this.state.info?.componentStack || this.state.error?.stack}
          </pre>
          <div style={{ textAlign: 'center' }}>
            <button
              style={{ marginTop: '1rem', padding: '0.5rem 1.5rem', background: '#00A3FF', color: '#fff', border: 'none', borderRadius: '8px', cursor: 'pointer' }}
              onClick={() => { localStorage.removeItem('cart-store'); localStorage.removeItem('auth-store'); window.location.reload() }}
            >
              נקה מטמון וטען מחדש
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

export default function App() {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <Toaster
          position="top-center"
          toastOptions={{
            duration: 3500,
            style: { fontFamily: 'Rubik, Heebo, sans-serif', direction: 'rtl', textAlign: 'right' },
            success: { iconTheme: { primary: '#00A3FF', secondary: '#fff' } },
          }}
        />

        <Routes>
          {/* Public routes */}
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/reset-password" element={<ResetPassword />} />
          <Route path="/privacy" element={<Privacy />} />
          <Route path="/terms" element={<Terms />} />
          <Route path="/refund" element={<Refund />} />

          {/* Main app routes with top layout */}
          <Route path="/" element={<LandingPage />} />
          {/* /parts is public — guests can browse, auth only required for cart/quote */}
          <Route
            path="/parts"
            element={
              <Layout>
                <Parts />
              </Layout>
            }
          />
          <Route
            path="/chat"
            element={
              <ProtectedRoute>
                <Layout>
                  <Chat />
                </Layout>
              </ProtectedRoute>
            }
          />
          <Route
            path="/orders"
            element={
              <ProtectedRoute>
                <Layout>
                  <Orders />
                </Layout>
              </ProtectedRoute>
            }
          />
          <Route
            path="/cart"
            element={
              <ProtectedRoute>
                <Layout>
                  <Cart />
                </Layout>
              </ProtectedRoute>
            }
          />
          <Route
            path="/profile"
            element={
              <ProtectedRoute>
                <Layout>
                  <Profile />
                </Layout>
              </ProtectedRoute>
            }
          />
          <Route
            path="/account"
            element={
              <ProtectedRoute>
                <Layout>
                  <ClientPortal />
                </Layout>
              </ProtectedRoute>
            }
          />
          <Route
            path="/agents"
            element={
              <ProtectedRoute adminOnly>
                <Layout>
                  <Agents />
                </Layout>
              </ProtectedRoute>
            }
          />
          <Route
            path="/payment/success"
            element={
              <ProtectedRoute>
                <Layout>
                  <PaymentSuccess />
                </Layout>
              </ProtectedRoute>
            }
          />

          {/* Admin tabbed module restored in top layout */}
          <Route
            path="/admin"
            element={
              <ProtectedRoute adminOnly>
                <Layout>
                  <Admin />
                </Layout>
              </ProtectedRoute>
            }
          />

          {/* Legacy deep links redirected to tabbed admin module */}
          <Route
            path="/admin/orders"
            element={
              <ProtectedRoute adminOnly>
                <Navigate to="/admin" replace />
              </ProtectedRoute>
            }
          />
          <Route
            path="/inventory"
            element={
              <ProtectedRoute adminOnly>
                <Navigate to="/admin" replace />
              </ProtectedRoute>
            }
          />

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </ErrorBoundary>
  )
}
