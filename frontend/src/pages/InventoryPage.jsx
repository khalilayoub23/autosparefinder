import React, { useState, useEffect } from 'react'
import DashboardLayout from '../components/Layout/DashboardLayout'
import InventoryItem, { SkeletonInventoryItem } from '../components/Inventory/InventoryTable'
import SearchFilters from '../components/Inventory/SearchFilters'
import toast from 'react-hot-toast'

const InventoryPage = () => {
  const [parts, setParts] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('הכל')
  const [licensePlate, setLicensePlate] = useState('')

  useEffect(() => {
    const fetchInventory = async () => {
      try {
        setLoading(true)
        const params = new URLSearchParams()
        if (search) params.append('search', search)
        if (category && category !== 'הכל') params.append('category', category)
        if (licensePlate) params.append('license_plate', licensePlate)

        const res = await fetch(`/api/inventory?${params.toString()}`, {
          headers: { Authorization: `Bearer ${localStorage.getItem('access_token')}` },
        })

        if (!res.ok) throw new Error('Failed to fetch inventory')

        const data = await res.json()
        setParts(data)

        if (search || licensePlate || category !== 'הכל') {
          toast(`נמצאו ${data.length} רשומות`, {
            position: 'bottom-left',
            style: {
              borderRadius: '14px',
              background: '#F1F5F9',
              color: '#1B2228',
              fontSize: '12px',
              fontWeight: 'bold',
              border: '1px solid #CBD5E1',
            },
            duration: 2000,
          })
        }
      } catch (err) {
        console.error(err)
        setError('שגיאה בטעינת מלאי החלפים')
      } finally {
        setLoading(false)
      }
    }

    const timeoutId = setTimeout(() => fetchInventory(), 500)
    return () => clearTimeout(timeoutId)
  }, [search, category, licensePlate])

  return (
    <DashboardLayout>
      <div className="mb-8 flex justify-between items-end">
        <div>
          <h2 className="text-2xl font-black text-[#1B2228]">ניהול מלאי חלפים</h2>
          <p className="text-sm text-slate-500">צפייה, עריכה וניהול של מלאי החלפים בזמן אמת</p>
        </div>
        <button className="bg-[#1B2228] text-white px-6 py-2 rounded-brand font-bold border border-cyan-300/50 shadow-sm hover:shadow-[0_10px_26px_rgba(0,204,255,0.2)] transition-all">
          + הוסף חלק חדש
        </button>
      </div>

      {error && (
        <div className="card p-4 mb-6 text-sm font-bold text-slate-600">
          {error}
        </div>
      )}

      <SearchFilters
        search={search}
        setSearch={setSearch}
        category={category}
        setCategory={setCategory}
        licensePlate={licensePlate}
        setLicensePlate={setLicensePlate}
      />

      <div className="card overflow-hidden relative min-h-[300px]">
        <table className="w-full text-right">
          <thead>
            <tr className="bg-slate-100 border-b border-slate-300">
              <th className="py-4 px-6 text-xs font-bold text-slate-500 uppercase">שם חלק</th>
              <th className="py-4 px-6 text-xs font-bold text-slate-500 uppercase">מק"ט</th>
              <th className="py-4 px-6 text-xs font-bold text-slate-500 uppercase">סטטוס מלאי</th>
              <th className="py-4 px-6 text-xs font-bold text-slate-500 uppercase text-left">מחיר ליחידה</th>
              <th className="py-4 px-6" />
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <>
                <SkeletonInventoryItem />
                <SkeletonInventoryItem />
                <SkeletonInventoryItem />
                <SkeletonInventoryItem />
                <SkeletonInventoryItem />
              </>
            ) : parts.length === 0 ? (
              <tr>
                <td colSpan="5" className="py-8 text-center text-slate-500 font-bold">לא נמצאו חלפים תואמים</td>
              </tr>
            ) : (
              parts.map((part) => <InventoryItem key={part.id} part={part} />)
            )}
          </tbody>
        </table>
      </div>
    </DashboardLayout>
  )
}

export default InventoryPage
