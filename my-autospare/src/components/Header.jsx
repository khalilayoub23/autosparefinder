import React, { useState } from "react";
import logo from "../assets/logo-attached-transparent.png";

const menuItems = [
  { label: "Home", href: "/" },
  { label: "Parts", href: "/parts" },
  { label: "Orders", href: "/orders" },
  { label: "Support", href: "/chat" },
];

function WhatsAppIcon({ className = "w-4 h-4" }) {
  return (
    <svg className={className} fill="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <path d="M12 2C6.477 2 2 6.477 2 12s4.477 10 10 10 10-4.477 10-10S17.523 2 12 2z" />
      <path d="M12 10c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z" />
      <path d="M12 14c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2-2z" />
      <path d="M12 18c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2-2z" />
      <path d="M12 22c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2-2z" />
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
      <div className="max-w-[1400px] mx-auto px-4 py-2.5 md:py-3 flex items-center justify-between gap-4 md:gap-5">
        <a href="/" className="flex-shrink-0">
          <img src={logo} alt="AutoSpare Finder" className="h-[86px] md:h-[94px] w-auto max-w-[128px] md:max-w-[144px] object-contain opacity-[0.95] drop-shadow-[0_5px_12px_rgba(0,0,0,0.22)]" />
        </a>

        <div className="flex-1 hidden md:flex items-center bg-white rounded-md overflow-hidden max-w-[700px] h-[52px] shadow-lg">
          <input
            type="text"
            className="px-3 h-full text-[13px] text-slate-600 outline-none bg-transparent cursor-pointer font-medium hover:bg-slate-50 transition-colors"
            placeholder="Search for parts..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          <div className="h-2/3 w-[1px] bg-slate-200"></div>
          <select className="px-3 h-full text-[13px] text-slate-600 outline-none bg-transparent cursor-pointer font-medium hover:bg-slate-50 transition-colors">
            <option value="">All Categories</option>
            <option value="engine">Engine</option>
            <option value="transmission">Transmission</option>
            <option value="brakes">Brakes</option>
            <option value="steering">Steering</option>
            <option value="tires">Tires</option>
            <option value="electronics">Electronics</option>
            <option value="accessories">Accessories</option>
          </select>
          <button onClick={handleSearch} className="h-full bg-[#2563eb] hover:bg-[#1d4ed8] px-6 transition-colors flex items-center justify-center">
            <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" /></svg>
          </button>
        </div>

        <nav className="flex items-center gap-4">
          {menuItems.map((item) => (
            <a key={item.href} href={item.href}>
              {item.label}
            </a>
          ))}
        </nav>
      </div>
    </header>
  );
}
