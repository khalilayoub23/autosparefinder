import React from 'react';

const SearchFilters = ({ search, setSearch, category, setCategory, licensePlate, setLicensePlate }) => {
  return (
    <div className="bg-white p-4 rounded-brand border border-brand-border shadow-sm mb-6 flex flex-wrap gap-4 items-center">
      
      {/* License Plate Input - Specialized UI */}
      <div className="flex flex-col gap-1">
        <label className="text-[10px] font-bold text-slate-500 uppercase mr-1">חיפוש לפי מספר רכב</label>
        <div className="flex items-center bg-[#FFD700] border-2 border-black rounded-md px-2 py-1 w-40 h-10 shadow-sm">
          <div className="flex flex-col items-center justify-center border-l border-black/20 pl-2 ml-2 h-full">
             <div className="w-4 h-3 bg-blue-700"></div>
             <span className="text-[8px] font-bold text-blue-700 leading-none mt-0.5">IL</span>
          </div>
          <input 
            type="text" 
            placeholder="00-000-00" 
            value={licensePlate}
            onChange={(e) => setLicensePlate(e.target.value)}
            className="bg-transparent border-none outline-none text-black font-black text-center w-full placeholder:text-black/30 tracking-widest text-lg"
          />
        </div>
      </div>

      {/* Parts Search */}
      <div className="flex flex-col gap-1 flex-1 min-w-[200px]">
        <label className="text-[10px] font-bold text-slate-500 uppercase mr-1">חיפוש חלקי חילוף</label>
        <div className="relative">
          <input 
            type="text" 
            placeholder="חפש לפי שם חלק, מק״ט או קטגוריה..." 
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full h-10 bg-brand-surface border border-brand-border rounded-lg px-10 text-sm focus:border-brand-blue focus:ring-1 focus:ring-brand-blue outline-none transition-all"
          />
          <div className="absolute right-3 top-2.5 text-slate-400">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </div>
        </div>
      </div>

      {/* Category Dropdown */}
      <div className="flex flex-col gap-1">
        <label className="text-[10px] font-bold text-slate-500 uppercase mr-1">קטגוריה</label>
        <select 
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          className="h-10 bg-brand-surface border border-brand-border rounded-lg px-4 text-sm font-medium outline-none focus:border-brand-blue">
          <option value="הכל">כל הקטגוריות</option>
          <option value="בלמים">בלמים</option>
          <option value="מנוע">מנוע</option>
          <option value="חשמל">חשמל</option>
          <option value="מרכב">מרכב</option>
        </select>
      </div>
    </div>
  );
};

export default SearchFilters;
