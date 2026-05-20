import { useEffect, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../stores/authStore'

export default function ProtectedRoute({ children, adminOnly = false }) {
  const { user, fetchMe } = useAuthStore()
  const location = useLocation()
  const [checkingAuth, setCheckingAuth] = useState(true)

  useEffect(() => {
    let active = true
    const token = localStorage.getItem('access_token')

    if (!token) {
      setCheckingAuth(false)
      return
    }

    // If user already has role information, no need to re-fetch.
    if (user && typeof user.is_admin !== 'undefined') {
      setCheckingAuth(false)
      return
    }

    ;(async () => {
      try {
        await fetchMe()
      } finally {
        if (active) setCheckingAuth(false)
      }
    })()

    return () => {
      active = false
    }
  }, [user?.id, user?.is_admin])

  if (checkingAuth) {
    return (
      <div className="min-h-screen flex items-center justify-center text-gray-500">
        טוען הרשאות...
      </div>
    )
  }

  if (!user || !localStorage.getItem('access_token')) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  if (adminOnly && !user.is_admin) {
    return <Navigate to="/chat" replace />
  }

  return children
}
