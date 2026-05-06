import React, { useState, useEffect } from 'react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';

const SalesChart = () => {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchAnalytics = async () => {
      try {
        const res = await fetch('/api/admin/analytics', {
          headers: { 'Authorization': `Bearer ${localStorage.getItem('access_token')}` }
        });
        if (res.ok) {
          const analyticsData = await res.json();
          setData(analyticsData);
        }
      } catch (err) {
        console.error("Failed to load analytics", err);
      } finally {
        setLoading(false);
      }
    };
    
    fetchAnalytics();
  }, []);

  return (
    <div className="bg-white p-6 rounded-brand border border-brand-border shadow-sm h-full flex flex-col relative">
      <h3 className="text-lg font-bold text-brand-navy mb-6">פעילות חיפושים והזמנות (שבועי)</h3>
      {loading && <div className="absolute inset-0 bg-white/80 flex items-center justify-center z-10 font-bold text-brand-blue">טוען נתונים...</div>}
      <div className="flex-1 w-full min-h-[300px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="colorBlue" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#00A3FF" stopOpacity={0.1}/>
                <stop offset="95%" stopColor="#00A3FF" stopOpacity={0}/>
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E2E8F0" />
            <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fill: '#64748B', fontSize: 12 }} dy={10} />
            <YAxis axisLine={false} tickLine={false} tick={{ fill: '#64748B', fontSize: 12 }} dx={-10} />
            <Tooltip 
              contentStyle={{ borderRadius: '8px', border: '1px solid #E2E8F0', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
              itemStyle={{ color: '#0F172A', fontWeight: 'bold' }}
            />
            <Area type="monotone" dataKey="orders" name="הזמנות" stroke="#00A3FF" strokeWidth={3} fillOpacity={1} fill="url(#colorBlue)" />
            <Area type="monotone" dataKey="searches" name="חיפושים" stroke="#94A3B8" strokeWidth={2} fillOpacity={0.1} fill="#94A3B8" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

export default SalesChart;
