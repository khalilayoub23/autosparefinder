import { Navigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'

export default function ProtectedRoute({ children, adminOnly = false }) {
  const { user } = useAuthStore()
  const location = useLocation()

  if (!user || !localStorage.getItem('access_token')) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }
  if (adminOnly && !user.is_admin) {
    return <Navigate to="/" replace />
  }
  return children
}
