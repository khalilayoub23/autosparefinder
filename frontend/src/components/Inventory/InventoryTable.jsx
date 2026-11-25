import React from 'react'

export const SkeletonInventoryItem = () => (
  <tr className="border-b border-brand-border animate-pulse bg-brand-100/60">
    <td className="py-4 px-6"><div className="h-4 w-48 bg-brand-50 rounded" /></td>
    <td className="py-4 px-6"><div className="h-3 w-24 bg-brand-50 rounded" /></td>
    <td className="py-4 px-6"><div className="h-5 w-16 bg-brand-50 rounded-full" /></td>
    <td className="py-4 px-6"><div className="h-4 w-12 bg-brand-50 rounded" /></td>
    <td className="py-4 px-6 text-left"><div className="h-4 w-8 bg-brand-50 rounded inline-block" /></td>
  </tr>
)

const STATUS_STYLE = {
  in_stock: 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30',
  low_stock: 'bg-brand-100 text-slate-200 border border-brand-border',
  out_of_stock: 'bg-brand-50 text-slate-300 border border-brand-border',
}

const STATUS_LABEL = {
  in_stock: 'זמין',
  low_stock: 'מלאי נמוך',
  out_of_stock: 'חסר במלאי',
}

const InventoryItem = ({ part }) => {
  const status = part.status === 'low_stock' || part.status === 'in_stock' ? part.status : 'out_of_stock'

  return (
    <tr className="border-b border-brand-border hover:bg-[#F0F9FF] transition-colors">
      <td className="py-4 px-6 font-medium text-[#E2E8F0]">{part.name}</td>
      <td className="py-4 px-6 text-xs font-mono text-slate-400 uppercase">{part.sku}</td>
      <td className="py-4 px-6">
        <span className={`px-3 py-1 rounded-full text-[10px] font-bold uppercase ${STATUS_STYLE[status]}`}>
          {STATUS_LABEL[status]}
        </span>
      </td>
      <td className="py-4 px-6 font-black text-[#E2E8F0] text-left">₪{part.price}</td>
      <td className="py-4 px-6 text-left">
        <button className="text-cyan-300 hover:text-cyan-300 font-bold text-sm ml-4">ערוך</button>
      </td>
    </tr>
  )
}

export default InventoryItem
