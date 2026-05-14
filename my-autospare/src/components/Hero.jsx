import React, { useMemo, useState } from "react";
import hero from "../assets/hero.svg";

const tabs = ["Search by VIN", "OEM Number", "SKU / Part Number", "Vehicle Details"];

export default function Hero() {
  const [activeTab, setActiveTab] = useState(tabs[0]);
  const [query, setQuery] = useState("");
  const [showResult, setShowResult] = useState(false);

  const helperText = useMemo(() => {
    if (!showResult) return "";
    return "No results";
  }, [showResult]);

  const runSearch = () => {
    console.log({ tab: activeTab, value: query });
    setShowResult(true);
  };

  return (
    <section className="bg-[#03122d]">
      <div className="max-w-6xl mx-auto px-4 py-14">
        <div className="rounded-2xl overflow-hidden border border-white/10 bg-gradient-to-br from-[#2D5BE3] via-[#3F7BF0] to-[#7FB7FF]">
          <div className="grid lg:grid-cols-[1.1fr_1fr] gap-4 items-stretch">
            <div className="p-8 md:p-10 text-white">
              <h1 className="text-[34px] md:text-[40px] leading-[1.1] font-extrabold">
                Find the Right Part.<br />
                <span className="text-[#8FC1FF]">Fast.</span> Easy. Reliable.
              </h1>
              <p className="mt-4 text-base md:text-lg text-white/90 max-w-lg">
                Search millions of auto parts from trusted suppliers worldwide. Best prices. Fast delivery.
              </p>

              <div className="mt-6 rounded-xl border border-white/20 bg-[#06214f]/60 p-3">
                <div role="tablist" aria-label="Search tabs" className="grid grid-cols-2 md:grid-cols-4 gap-2">
                  {tabs.map((tab) => {
                    const active = tab === activeTab;
                    return (
                      <button
                        key={tab}
                        aria-label={tab}
                        aria-pressed={active}
                        onClick={() => setActiveTab(tab)}
                        className="text-xs md:text-sm rounded-md px-2 py-2 font-medium"
                        style={{ backgroundColor: active ? "#2D5BE3" : "#DDDDDD", color: active ? "#FFFFFF" : "#10233f" }}
                      >
                        {tab}
                      </button>
                    );
                  })}
                </div>

                <div className="mt-3 flex flex-col sm:flex-row gap-2">
                  <input
                    aria-label="Hero search input"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Enter VIN Number (e.g., 1HGCM82633A004352)"
                    className="flex-1 rounded-md border border-white/30 bg-white px-4 py-3 text-sm text-slate-700 outline-none"
                  />
                  <button aria-label="Search parts in hero" onClick={runSearch} className="rounded-md bg-brandBlue hover:bg-[#2449ba] px-6 py-3 text-sm font-semibold">
                    Search Parts
                  </button>
                </div>
                {helperText ? <p className="mt-2 text-sm text-white">{helperText}</p> : null}

                <div className="mt-3 text-xs text-white/90 flex flex-wrap gap-4">
                  <span>100% Safe &amp; Secure Search</span>
                  <span>We don&apos;t store your VIN</span>
                </div>
              </div>
            </div>

            <div className="relative min-h-[320px] lg:min-h-[520px] flex items-end justify-center p-4">
              <img src={hero} alt="AUTOSPAREFINDER hero automotive parts illustration" className="w-full h-auto object-contain" />
            </div>
          </div>
        </div>

        <div className="grid grid-cols-2 lg:grid-cols-5 gap-2 mt-3 text-white text-sm">
          {[
            ["Trusted Suppliers", "1000+ verified suppliers"],
            ["Best Prices", "Compare & save more"],
            ["Fast Delivery", "Local & international"],
            ["Secure Payments", "100% secure checkout"],
            ["Expert Support", "24/7 support"]
          ].map(([title, text]) => (
            <div key={title} className="rounded-lg border border-white/20 bg-[#03193f] px-4 py-3">
              <p className="font-semibold">{title}</p>
              <p className="text-xs text-slate-300 mt-1">{text}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
