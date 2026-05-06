import React from 'react'

export const SkeletonInventoryItem = () => (
  <tr className="border-b border-slate-300 animate-pulse bg-slate-50/60">
    <td className="py-4 px-6"><div className="h-4 w-48 bg-slate-200 rounded" /></td>
    <td className="py-4 px-6"><div className="h-3 w-24 bg-slate-200 rounded" /></td>
    <td className="py-4 px-6"><div className="h-5 w-16 bg-slate-200 rounded-full" /></td>
    <td className="py-4 px-6"><div className="h-4 w-12 bg-slate-200 rounded" /></td>
    <td className="py-4 px-6 text-left"><div className="h-4 w-8 bg-slate-200 rounded inline-block" /></td>
  </tr>
)

const STATUS_STYLE = {
  in_stock: 'bg-cyan-50 text-cyan-700 border border-cyan-200',
  low_stock: 'bg-slate-100 text-slate-700 border border-slate-300',
  out_of_stock: 'bg-slate-200 text-slate-600 border border-slate-300',
}

const STATUS_LABEL = {
  in_stock: 'זמין',
  low_stock: 'מלאי נמוך',
  out_of_stock: 'חסר במלאי',
}

const InventoryItem = ({ part }) => {
  const status = part.status === 'low_stock' || part.status === 'in_stock' ? part.status : 'out_of_stock'

  return (
    <tr className="border-b border-slate-300 hover:bg-[#F0F9FF] transition-colors">
      <td className="py-4 px-6 font-medium text-[#1B2228]">{part.name}</td>
      <td className="py-4 px-6 text-xs font-mono text-slate-500 uppercase">{part.sku}</td>
      <td className="py-4 px-6">
        <span className={`px-3 py-1 rounded-full text-[10px] font-bold uppercase ${STATUS_STYLE[status]}`}>
          {STATUS_LABEL[status]}
        </span>
      </td>
      <td className="py-4 px-6 font-black text-[#1B2228] text-left">₪{part.price}</td>
      <td className="py-4 px-6 text-left">
        <button className="text-cyan-600 hover:text-cyan-700 font-bold text-sm ml-4">ערוך</button>
      </td>
    </tr>
  )
}

export default InventoryItem
