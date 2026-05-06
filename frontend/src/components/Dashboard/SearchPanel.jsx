import React from 'react';

const SearchPanel = ({ title, icon: Icon, subtitle, active, onClick }) => {
  return (
    <button 
      onClick={onClick}
      className={`flex flex-col items-center justify-center p-5 rounded-brand border transition-all duration-200
        ${active 
          ? 'bg-brand-blue/10 border-brand-blue shadow-electric scale-[1.02]' 
          : 'bg-white border-brand-border hover:border-brand-blue/50 hover:shadow-md'
        }`}
    >
      <div className={`text-2xl mb-2 ${active ? 'text-brand-blue' : 'text-slate-600'}`}>
        <Icon />
      </div>
      <span className="text-sm font-bold text-brand-navy">{title}</span>
      <span className="text-[10px] text-slate-500 mt-1 uppercase tracking-wide">{subtitle}</span>
    </button>
  );
};

export default SearchPanel;
