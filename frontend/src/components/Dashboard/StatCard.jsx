import React from 'react'

export const SkeletonStatCard = () => (
  <div className="card p-5 flex items-center gap-4 animate-pulse">
    <div className="w-12 h-12 rounded-xl border border-slate-300 bg-slate-100" />
    <div className="relative flex-1 text-right ml-2 lg:ml-0 flex flex-col items-end">
      <div className="h-2 w-20 bg-slate-200 rounded mb-2" />
      <div className="h-6 w-12 bg-slate-200 rounded" />
    </div>
  </div>
)

const StatCard = ({ label, value, icon: Icon }) => (
  <div className="card p-5 flex items-center gap-4">
    <div className="w-12 h-12 rounded-xl flex items-center justify-center border border-cyan-300 bg-cyan-50 text-[#1B2228] shadow-[0_0_0_1px_rgba(0,204,255,0.08)]">
      <Icon className="w-5 h-5" />
    </div>
    <div className="text-right w-full">
      <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">{label}</p>
      <h3 className="text-2xl font-black text-[#1B2228] leading-tight">{value}</h3>
    </div>
  </div>
)

export default StatCard
