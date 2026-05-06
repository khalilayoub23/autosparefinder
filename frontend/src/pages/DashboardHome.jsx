import React, { useEffect, useState } from 'react'
import StatCard, { SkeletonStatCard } from '../components/Dashboard/StatCard'
import SalesChart from '../components/Dashboard/SalesChart'
import ActivityFeed from '../components/Dashboard/ActivityFeed'
import { FaUsers, FaChartLine, FaClock } from 'react-icons/fa'
import toast from 'react-hot-toast'
import api from '../api/client'

const INITIAL_STATS = {
  pending_orders: 0,
  total_orders: 0,
  total_users: 0,
}

function normalizeStats(raw = {}) {
  return {
    pending_orders: Number(raw.pending_orders ?? raw.pendingOrders ?? 0),
    total_orders: Number(raw.total_orders ?? raw.totalOrders ?? 0),
    total_users: Number(raw.total_users ?? raw.totalUsers ?? 0),
  }
}

const DashboardHome = () => {
  const [stats, setStats] = useState(INITIAL_STATS)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let mounted = true
    let currentPending = 0

    const fetchDashboardData = async (isPolling = false) => {
      try {
        if (!isPolling && mounted) {
          setLoading(true)
          setError(null)
        }

        const { data } = await api.get('/admin/stats')
        const nextStats = normalizeStats(data)

        if (!mounted) return

        if (isPolling && nextStats.pending_orders > currentPending) {
          toast.success('הזמנה חדשה התקבלה במערכת!', {
            position: 'bottom-left',
            style: { borderRadius: '14px', background: '#00CCFF', color: '#0f172a', fontWeight: 'bold' },
          })
        }

        currentPending = nextStats.pending_orders
        setStats(nextStats)
      } catch (err) {
        if (!mounted) return
        if (!isPolling) {
          console.error(err)
          setError('שגיאה בטעינת נתונים')
        }
      } finally {
        if (!isPolling && mounted) setLoading(false)
      }
    }

    fetchDashboardData(false)
    const interval = setInterval(() => fetchDashboardData(true), 15000)

    return () => {
      mounted = false
      clearInterval(interval)
    }
  }, [])

  return (
    <div className="space-y-6 lg:space-y-8">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-black text-[#1B2228]">לוח בקרה</h1>
      </div>

      {error && (
        <div className="card p-4 text-sm font-bold text-slate-600">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {loading ? (
          <>
            <SkeletonStatCard />
            <SkeletonStatCard />
            <SkeletonStatCard />
          </>
        ) : (
          <>
            <StatCard label="הזמנות ממתינות" value={stats.pending_orders} icon={FaClock} />
            <StatCard label="סה״כ הזמנות" value={stats.total_orders} icon={FaChartLine} />
            <StatCard label="סה״כ משתמשים" value={stats.total_users} icon={FaUsers} />
          </>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 h-[400px]">
        <div className="lg:col-span-2 h-full">
          <SalesChart />
        </div>
        <div className="lg:col-span-1 h-full">
          <ActivityFeed />
        </div>
      </div>
    </div>
  )
}

export default DashboardHome
