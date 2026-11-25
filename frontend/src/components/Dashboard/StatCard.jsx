import React from 'react'

export const SkeletonStatCard = () => (
  <div className="card p-5 flex items-center gap-4 animate-pulse">
    <div className="w-12 h-12 rounded-xl border border-brand-border bg-brand-100" />
    <div className="relative flex-1 text-right ml-2 lg:ml-0 flex flex-col items-end">
      <div className="h-2 w-20 bg-brand-50 rounded mb-2" />
      <div className="h-6 w-12 bg-brand-50 rounded" />
    </div>
  </div>
)

const StatCard = ({ label, value, icon: Icon }) => (
  <div className="card p-5 flex items-center gap-4">
    <div className="w-12 h-12 rounded-xl flex items-center justify-center border border-cyan-500/30 bg-cyan-500/15 text-[#E2E8F0] shadow-[0_0_0_1px_rgba(0,204,255,0.08)]">
      <Icon className="w-5 h-5" />
    </div>
    <div className="text-right w-full">
      <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">{label}</p>
      <h3 className="text-2xl font-black text-[#E2E8F0] leading-tight">{value}</h3>
    </div>
  </div>
)

export default StatCard
