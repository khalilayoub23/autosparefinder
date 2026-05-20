import React, { useState } from "react";

const menuItems = [
  { label: "Home", href: "/" },
  { label: "Categories", href: "#categories", hasChevron: true },
  { label: "Catalog", href: "/parts" },
  { label: "Request a Quote", href: "/parts" },
  { label: "How It Works", href: "#how" },
  { label: "About Us", href: "#about" },
  { label: "Support", href: "/chat" },
];

function WhatsAppIcon({ className = "w-4 h-4" }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 2.4a9.6 9.6 0 0 0-8.18 14.63L3 21l4.17-1.1A9.6 9.6 0 1 0 12 2.4Z"
        fill="#25D366"
      />
      <path
        d="m8.8 7.74-.58.05a1 1 0 0 0-.71.42c-.48.68-1.09 1.92-.4 3.8.9 2.44 3.46 4.88 5.73 5.76 1.18.46 2.11.15 2.75-.2.35-.19.56-.54.6-.93l.04-.55a.68.68 0 0 0-.43-.68l-2.18-.8a.68.68 0 0 0-.77.22l-.43.57a.58.58 0 0 1-.64.19 6.35 6.35 0 0 1-3.12-3.1.58.58 0 0 1 .18-.65l.57-.44a.68.68 0 0 0 .23-.77l-.81-2.16a.68.68 0 0 0-.63-.43Z"
        fill="#fff"
      />
    </svg>
  );
}

export default function Header() {
  const [searchQuery, setSearchQuery] = useState("");
  const isOrdersPage = window.location.pathname === "/orders";

  const handleSearch = () => {
    const q = searchQuery.trim();
    if (q) {
      window.location.href = `/parts?search=${encodeURIComponent(q)}`;
    }
  };

  return (
    <header className="bg-[#021737] text-white font-sans">
      <div className="border-b border-white/10 text-[12px] text-slate-300 bg-[#010e24]">
        <div className="max-w-[1400px] mx-auto px-4 py-2.5 flex md:items-center flex-col md:flex-row justify-between gap-3">
          <div className="flex items-center gap-2">
            <svg className="w-4 h-4 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
            <p>Millions of Parts. Multiple Suppliers. The Right Part for Your Vehicle.</p>
          </div>
          <div className="flex items-center gap-6 font-medium">
            {!isOrdersPage && (
              <a href="/chat" className="hover:text-white cursor-pointer transition-colors">Support</a>
            )}
            {!isOrdersPage && (
              <a href="/orders" className="hover:text-white cursor-pointer transition-colors">Track Order</a>
            )}
            <a href="https://wa.me/972000000000" target="_blank" rel="noreferrer" className="flex items-center gap-1.5 cursor-pointer font-semibold text-slate-200 hover:text-white transition-colors">
              <WhatsAppIcon className="w-4 h-4" />
              WhatsApp
            </a>
            <details className="relative" aria-label="Language selector">
              <summary className="list-none cursor-pointer flex items-center gap-1 rounded-md border border-white/10 px-1.5 py-1 hover:bg-white/10 transition-colors">
                <img src="https://flagcdn.com/w20/us.png" alt="Language" className="w-4 h-auto rounded-sm" />
                                <svg className="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
              </summary>
              <div className="absolute right-0 mt-2 w-36 rounded-md border border-white/15 bg-[#021737] shadow-lg overflow-hidden z-20">
                <a href="?lang=he" aria-label="Hebrew" title="Hebrew" className="flex items-center gap-2 px-2.5 py-1.5 text-[12px] hover:bg-white/10 transition-colors">
                  <img src="https://flagcdn.com/w20/il.png" alt="Hebrew" className="w-4 h-auto rounded-sm" />
                  <span>Hebrew</span>
                </a>
                <a href="?lang=ar" aria-label="Arabic" title="Arabic" className="flex items-center gap-2 px-2.5 py-1.5 text-[12px] hover:bg-white/10 transition-colors">
                  <img src="https://flagcdn.com/w20/sa.png" alt="Arabic" className="w-4 h-auto rounded-sm" />
                  <span>Arabic</span>
                </a>
                <a href="?lang=en" aria-label="English" title="English" className="flex items-center gap-2 px-2.5 py-1.5 text-[12px] hover:bg-white/10 transition-colors">
                  <img src="https://flagcdn.com/w20/us.png" alt="English" className="w-4 h-auto rounded-sm" />
                  <span>English</span>
                </a>
              </div>
            </details>
          </div>
        </div>
      </div>

      <div className="max-w-[1400px] mx-auto px-4 py-2.5 md:py-3 flex items-center justify-between gap-4 md:gap-5">
        <a href="/" className="flex-shrink-0">
          <img src="/logo-tests/autosparefinder-logo-header.svg" alt="AutoSpare Finder" className="h-[86px] md:h-[94px] w-auto max-w-[128px] md:max-w-[144px] object-contain opacity-[0.95] drop-shadow-[0_5px_12px_rgba(0,0,0,0.22)]" />
        </a>

        <div className="flex-1 hidden md:flex items-center bg-white rounded-md overflow-hidden max-w-[700px] h-[52px] shadow-lg">
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            className="flex-1 px-5 py-2 text-[15px] text-slate-800 outline-none h-full placeholder:text-slate-400"
            placeholder="Search by VIN, OEM Number, SKU or Part Name..."
          />
          <div className="h-2/3 w-[1px] bg-slate-200"></div>
          <select className="px-3 h-full text-[13px] text-slate-600 outline-none bg-transparent cursor-pointer font-medium hover:bg-slate-50 transition-colors">
            <option>All Categories</option>
          </select>
          <button onClick={handleSearch} className="h-full bg-[#2563eb] hover:bg-[#1d4ed8] px-6 transition-colors flex items-center justify-center">
            <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" /></svg>
          </button>
        </div>

        <div className="hidden lg:flex items-center gap-4 text-[13px] font-medium text-slate-100 flex-shrink-0">
          <a href="/chat" className="flex items-center gap-3 cursor-pointer hover:text-white transition-colors group">
            <svg className="w-6 h-6 text-slate-300 group-hover:text-white transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" /></svg>
            <div className="flex flex-col leading-tight">
              <span className="font-bold">Chat with us</span>
              <span className="text-[12px] text-slate-400">We're online</span>
            </div>
          </a>
          <a href="/login" className="flex items-center gap-3 cursor-pointer hover:text-white transition-colors group">
            <svg className="w-6 h-6 text-slate-300 group-hover:text-white transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" /></svg>
            <div className="flex flex-col leading-tight">
              <span className="font-bold">Sign In</span>
              <span className="text-[12px] text-slate-400">My Account</span>
            </div>
          </a>
          <a href="/cart" className="flex items-center gap-3 cursor-pointer hover:text-white transition-colors group relative">
            <div className="relative">
              <svg className="w-6 h-6 text-slate-300 group-hover:text-white transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 11-4 0 2 2 0 014 0z" /></svg>
              <div className="absolute -top-1.5 -right-2 bg-[#2563eb] text-white text-[11px] w-5 h-5 rounded-full flex items-center justify-center font-bold shadow-sm">0</div>
            </div>
            <span className="font-bold">Cart</span>
          </a>
        </div>
      </div>

      <nav className="bg-[#021737] border-t border-white/5 border-b border-white/5">
        <div className="max-w-[1400px] mx-auto px-4 flex items-center gap-2 overflow-x-hidden whitespace-nowrap text-[15px] font-semibold">
          {menuItems.map((item, idx) => (
            <a
              key={item.label}
              href={item.href}
              className={`px-6 py-3.5 transition-colors flex items-center gap-1.5 ${
                idx === 0
                  ? "bg-[#2563eb] text-white rounded-t-sm"
                  : "text-slate-300 hover:text-white hover:bg-white/5 rounded-t-sm"
              }`}
            >
              {item.label}
              {item.hasChevron && (
                <svg className="w-4 h-4 ml-0.5 opacity-70" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
              )}
            </a>
          ))}
        </div>
      </nav>
    </header>
  );
}
