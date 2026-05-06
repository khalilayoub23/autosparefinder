import React from 'react';
import { FaBoxOpen, FaClipboardList, FaUserPlus, FaTruck } from 'react-icons/fa';

const activities = [
  { id: 1, type: 'order', text: 'הזמנה חדשה #1004 נוצרה', time: 'לפני 5 דקות', icon: FaBoxOpen, color: 'bg-blue-100 text-blue-600' },
  { id: 2, type: 'inventory', text: 'מלאי עודכן עבור רפידות בלם טויוטה', time: 'לפי שעה', icon: FaClipboardList, color: 'bg-slate-100 text-slate-600' },
  { id: 3, type: 'user', text: 'לקוח חדש נרשם במערכת', time: 'לפני שעתיים', icon: FaUserPlus, color: 'bg-green-100 text-green-600' },
  { id: 4, type: 'shipment', text: 'הזמנה #998 סומנה כנשלחה', time: 'לפי 4 שעות', icon: FaTruck, color: 'bg-amber-100 text-amber-600' },
  { id: 5, type: 'inventory', text: 'התראת מלאי נמוך: מסנן שמן מאזדה', time: 'אתמול', icon: FaClipboardList, color: 'bg-red-100 text-red-600' },
];

const ActivityFeed = () => {
  return (
    <div className="bg-white p-6 rounded-brand border border-brand-border shadow-sm h-full overflow-hidden flex flex-col">
      <div className="flex items-center justify-between mb-6">
        <h3 className="text-lg font-bold text-brand-navy">פעילות אחרונה</h3>
      </div>
      
      <div className="flex-1 overflow-auto pr-2">
        <div className="relative border-r-2 border-slate-100 right-[15px] space-y-6 pb-4">
          {activities.map((item, index) => {
            const Icon = item.icon;
            return (
              <div key={item.id} className="relative flex items-center pr-6 gap-4">
                <div className={`absolute -right-[17px] top-1/2 -translate-y-1/2 w-8 h-8 rounded-full border-2 border-white flex items-center justify-center ${item.color}`}>
                  <Icon className="text-xs" />
                </div>
                <div>
                  <p className="text-sm font-medium text-slate-800">{item.text}</p>
                  <p className="text-[10px] font-bold text-slate-400 mt-1">{item.time}</p>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

export default ActivityFeed;
