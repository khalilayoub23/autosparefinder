import React from "react";
import logo from "../assets/logo.png";

const menuItems = ["Home", "Categories", "Catalog", "Request a Quote", "How It Works", "About Us", "Support"];

export default function Header() {
  return (
    <header className="bg-[#021737] text-white">
      <div className="border-b border-white/10 text-xs text-slate-300">
        <div className="max-w-6xl mx-auto px-4 py-2 flex items-center justify-between gap-3">
          <p>Millions of Parts. Multiple Suppliers. The Right Part for Your Vehicle.</p>
          <div className="flex items-center gap-5">
            <span>Support</span>
            <span>Track Order</span>
            <span>WhatsApp</span>
            <span>USD</span>
          </div>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-4">
        <img src={logo} alt="AUTOSPAREFINDER logo" className="h-14 w-auto object-contain" />

        <div className="flex-1 hidden md:flex items-center bg-white rounded-md overflow-hidden">
          <input
            aria-label="Search by VIN OEM SKU or part name"
            className="w-full px-4 py-3 text-sm text-slate-700 outline-none"
            placeholder="Search by VIN, OEM Number, SKU or Part Name..."
          />
          <select aria-label="Select category" className="px-3 py-3 text-sm text-slate-600 border-l border-slate-200 outline-none">
            <option>All Categories</option>
          </select>
          <button aria-label="Search parts" className="bg-brandBlue hover:bg-[#2449ba] px-4 py-3">
            Search
          </button>
        </div>

        <div className="hidden lg:flex items-center gap-5 text-xs text-slate-100">
          <span>Chat with us</span>
          <span>Sign In</span>
          <span>Cart</span>
        </div>
      </div>

      <nav className="border-t border-white/10 border-b border-white/10">
        <div className="max-w-6xl mx-auto px-4 py-1 flex items-center gap-2 overflow-x-auto whitespace-nowrap text-sm">
          {menuItems.map((item, idx) => (
            <button
              key={item}
              aria-label={item}
              className={`px-4 py-2 rounded-md ${idx === 0 ? "bg-brandBlue text-white" : "text-slate-200 hover:text-white hover:bg-white/10"}`}
            >
              {item}
            </button>
          ))}
        </div>
      </nav>
    </header>
  );
}
