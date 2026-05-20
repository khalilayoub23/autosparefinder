import React, { useMemo, useState } from "react";
import hero from "../assets/hero-cutout.png";

const tabs = [
  { name: "Search by VIN", icon: "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z", badge: true },
  { name: "OEM Number", icon: "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" },
  { name: "SKU / Part Number", icon: "M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z" },
  { name: "Vehicle Details", icon: "M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" }
];

export default function Hero() {
  const [activeTab, setActiveTab] = useState(tabs[0].name);
  const [query, setQuery] = useState("");
  const [showResult, setShowResult] = useState(false);
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);

  const helperText = useMemo(() => {
    if (loading) return "Searching...";
    if (showResult && results.length === 0) return "Redirecting to search...";
    if (showResult && results.length > 0) return `Redirecting with ${results.length} results...`;
    return "";
  }, [showResult, results, loading]);

  const runSearch = () => {
    const q = query.trim();
    if (!q) return;
    const mode = activeTab === "Search by VIN"
      ? "vin"
      : activeTab === "OEM Number"
      ? "oem"
      : activeTab === "SKU / Part Number"
      ? "sku"
      : "vehicle";
    window.location.href = `/parts?search=${encodeURIComponent(q)}&mode=${mode}`;
  };

  return (
    <section className="bg-[#021431] relative overflow-hidden">
      <div className="absolute inset-0 z-0">
        <div className="absolute inset-0 bg-gradient-to-br from-[#021737] via-[#021c43] to-[#042456] opacity-90"></div>
        <div className="absolute top-[20%] right-[-10%] w-[70%] max-w-[1000px] h-full bg-[radial-gradient(ellipse_at_center,rgba(45,91,227,0.15),transparent_60%)] pointer-events-none"></div>
      </div>

      <div className="max-w-[1300px] mx-auto px-4 py-6 lg:py-12">
        <div className="grid md:grid-cols-2 gap-0 items-stretch relative z-10">
          <div className="text-white pt-4">
            <h1 className="text-[38px] md:text-[52px] lg:text-[60px] leading-[1.05] font-extrabold tracking-tight">
              Find the Right Part.<br />
              <span className="text-[#3b82f6]">Fast.</span> Easy. <span className="text-[#3b82f6]">Reliable.</span>
            </h1>
            <p className="mt-5 text-[15px] md:text-[17px] text-slate-300 max-w-lg leading-relaxed">
              Search millions of auto parts from trusted suppliers worldwide. Best prices. Fast delivery.
            </p>

            <div className="mt-8 rounded-2xl border border-white/5 bg-[#0a1e3f]/60 p-4 lg:p-5 shadow-2xl backdrop-blur-md">
              <div role="tablist" aria-label="Search tabs" className="flex flex-wrap gap-2 mb-3">
                {tabs.map((tab) => {
                  const active = tab.name === activeTab;
                  return (
                    <button
                      key={tab.name}
                      aria-label={tab.name}
                      aria-pressed={active}
                      onClick={() => setActiveTab(tab.name)}
                      className={`text-[12px] md:text-[13px] rounded-lg border border-transparent flex items-center gap-1.5 px-3 lg:px-4 py-2 font-medium transition-all ${
                        active ? "bg-[#2563eb] text-white border-[#3b82f6]/50 shadow-[0_0_15px_rgba(37,99,235,0.3)]" : "bg-[#0f274e] text-slate-300 hover:text-white border-white/5 hover:bg-[#153468]"
                      }`}
                    >
                      <svg className="w-4 h-4 opacity-80" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={tab.icon} /></svg>
                      {tab.name}
                    </button>
                  );
                })}
              </div>

              <div className="flex flex-col sm:flex-row gap-0 rounded-xl overflow-hidden bg-white shadow-inner focus-within:ring-2 focus-within:ring-[#3b82f6] transition-shadow mt-2 relative">
                <div className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400">
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" /></svg>
                </div>
                <input
                  aria-label="Hero search input"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={activeTab === "Search by VIN" ? "Enter VIN Number (e.g., 1HGCM82633A004352)" : "Enter Part Number..."}
                  className="flex-1 pl-12 pr-4 py-4 md:py-4 md:text-[15px] text-slate-800 outline-none w-full font-medium"
                  onKeyDown={(e) => e.key === "Enter" && runSearch()}
                />
                <button aria-label="Search parts in hero" onClick={runSearch} disabled={loading} className="bg-[#2563eb] hover:bg-[#1d4ed8] px-8 py-4 md:py-4 text-white font-bold transition-colors whitespace-nowrap shadow-lg">
                  Search Parts
                </button>
              </div>
              {helperText && <p className="mt-3 text-[13px] text-[#8FC1FF] font-medium px-1">{helperText}</p>}

              <div className="mt-4 text-[12px] text-slate-400 flex items-center flex-wrap gap-4 px-1">
                <span className="flex items-center gap-1.5">
                  <svg className="w-3.5 h-3.5 text-emerald-400" fill="currentColor" viewBox="0 0 20 20"><path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" /></svg>
                  100% Safe &amp; Secure Search
                </span>
                <span className="flex items-center gap-1.5">
                  <div className="w-1 h-1 rounded-full bg-slate-500"></div>
                  We don&apos;t store your VIN
                </span>
              </div>
            </div>
          </div>

          <div className="relative hidden md:flex items-center justify-center overflow-hidden">
            <div className="absolute inset-x-8 bottom-0 h-[58%] bg-[radial-gradient(ellipse_at_center,rgba(45,91,227,0.22),rgba(2,20,49,0)_72%)] pointer-events-none"></div>
            <img
              src={hero}
              alt="Auto Parts Illustration"
              className="w-[96%] max-w-[760px] h-auto object-contain opacity-[0.98] drop-shadow-[0_20px_38px_rgba(0,0,0,0.48)] -translate-y-8 lg:-translate-y-10 select-none [mask-image:linear-gradient(to_bottom,black_0%,black_94%,transparent_100%)]"
              draggable={false}
            />
          </div>
        </div>
      </div>

      <div className="bg-[#021737] border-y border-white/10 relative z-20">
        <div className="max-w-[1300px] mx-auto px-4">
          <div className="grid grid-cols-2 md:grid-cols-5 divide-x divide-white/10">
            {[
              ["Trusted Suppliers", "1000+ verified suppliers", "M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"],
              ["Best Prices", "Compare & save more", "M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z"],
              ["Fast Delivery", "Local & international", "M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4"],
              ["Secure Payments", "100% secure checkout", "M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"],
              ["Expert Support", "24/7 support", "M18.364 5.636l-3.536 3.536m0 5.656l3.536 3.536M9.172 9.172L5.636 5.636m3.536 9.192l-3.536 3.536M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-5 0a4 4 0 11-8 0 4 4 0 018 0z"]
            ].map(([title, text, icon]) => (
              <div key={title} className="flex flex-col items-center md:items-start text-center md:text-left py-4 px-2 lg:px-6 hover:bg-white/5 transition-colors">
                <div className="flex items-center gap-2 lg:gap-3 mb-1 justify-center md:justify-start w-full">
                  <svg className="w-5 h-5 lg:w-6 lg:h-6 text-slate-300 stroke-1" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d={icon} /></svg>
                  <p className="font-semibold text-[13px] md:text-[14px] text-white whitespace-nowrap">{title}</p>
                </div>
                <p className="text-[11px] md:text-[12px] text-slate-400 w-full">{text}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
