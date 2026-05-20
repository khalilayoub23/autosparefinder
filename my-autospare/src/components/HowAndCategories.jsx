import React from "react";

const steps = [
  ["1", "Search", "Find parts by VIN, OEM, SKU or vehicle details."],
  ["2", "Compare", "Compare prices, condition, delivery & suppliers."],
  ["3", "Order or Quote", "Place an order or request a quote if stock is limited."],
  ["4", "Track & Receive", "Track your order and get support anytime."]
];

const categories = [
  ["Engine Parts", "25,000+ parts", "/part-family/cutouts/engine.png"],
  ["Brake System", "18,000+ parts", "/part-family/cutouts/brakes.png"],
  ["Suspension", "12,000+ parts", "/part-family/cutouts/suspension-steering.png"],
  ["Electrical", "20,000+ parts", "/part-family/cutouts/electrical-sensors.png"],
  ["Body Parts", "15,000+ parts", "/part-family/cutouts/body-exterior.png"],
  ["Transmission", "8,000+ parts", "/part-family/cutouts/gearbox.png"],
  ["Cooling System", "7,000+ parts", "/part-family/cutouts/cooling.png"],
  ["Filters", "6,000+ parts", "/part-family/cutouts/filters.png"],
  ["Exhaust System", "9,000+ parts", "/part-family/cutouts/exhaust.png"],
  ["More Categories", "Browse all", "", true]
];

export default function HowAndCategories() {
  const go = (href) => () => { window.location.href = href; };
  return (
    <section className="bg-[#F8F9FA]" id="how">
      <div className="max-w-7xl mx-auto px-4 py-12">
        <div className="grid lg:grid-cols-[280px_1fr] gap-10">
          <div id="categories">
            <h2 className="text-xl md:text-2xl font-bold text-[#021737] mb-8">
              How <span className="text-[#1746A2]">AutoSpareFinder</span> Works
            </h2>
            <div className="relative">
              <div className="absolute left-[19px] top-4 bottom-4 w-px border-l-2 border-dashed border-[#DEE2E6]"></div>
              <div className="space-y-8">
                {steps.map((step) => (
                  <div key={step[0]} className="flex items-start gap-4 relative z-10">
                    <div className="h-10 w-10 shrink-0 rounded-full bg-[#2563eb] text-white flex items-center justify-center font-bold shadow-[0_0_0_6px_#F8F9FA]">{step[0]}</div>
                    <div className="pt-1">
                      <div className="flex items-center gap-2 mb-1">
                        <p className="font-bold text-[15px] text-[#021737]">{step[1]}</p>
                      </div>
                      <p className="text-[13px] text-slate-500 leading-snug pr-4">{step[2]}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div>
            <div className="flex items-center justify-between mb-8">
              <h3 className="text-xl md:text-2xl font-bold text-[#021737]">Top Categories</h3>
              <button aria-label="View all categories" onClick={go('/parts')} className="text-[#2563eb] hover:text-[#1d4ed8] flex items-center gap-1 text-[13px] font-semibold transition-colors">
                View All Categories <span aria-hidden="true">&rarr;</span>
              </button>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
              {categories.map(([name, count, image, isMore]) => (
                <article key={name} className="group cursor-pointer" onClick={go('/parts')}>
                  <div className="h-full rounded-xl border border-slate-200 bg-white p-4 shadow-sm hover:shadow-md transition-all duration-300 hover:border-[#2563eb]/30 flex flex-col items-center justify-center text-center">
                    <div className="w-full h-24 mb-3 flex items-center justify-center rounded-lg bg-transparent">
                      {isMore ? (
                        <div className="w-12 h-12 rounded-full bg-slate-100 flex items-center justify-center group-hover:bg-[#2563eb]/10 transition-colors">
                          <svg className="w-6 h-6 text-slate-400 group-hover:text-[#2563eb]" fill="currentColor" viewBox="0 0 24 24"><path d="M6 10c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm12 0c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm-6 0c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z" /></svg>
                        </div>
                      ) : (
                        <img
                          loading="lazy"
                          src={image}
                          alt={name}
                          className="max-h-full max-w-[85%] object-contain drop-shadow-[0_10px_18px_rgba(0,0,0,0.22)] group-hover:scale-105 transition-transform duration-300 [image-rendering:auto]"
                        />
                      )}
                    </div>
                    <p className="font-bold text-[13px] text-[#021737] leading-tight group-hover:text-[#2563eb] transition-colors">{name}</p>
                    <p className="text-[11px] text-slate-500 mt-1">{count}</p>
                  </div>
                </article>
              ))}
            </div>
          </div>
        </div>

        <div className="mt-12 flex flex-col lg:flex-row gap-4">
          <div className="lg:w-[35%] rounded-2xl p-6 bg-[#021737] text-white flex flex-col justify-center relative overflow-hidden border border-white/10 shadow-lg">
            <div className="absolute inset-0 bg-gradient-to-br from-[#021737] to-[#0f1f35] opacity-80"></div>
            <div className="relative z-10 flex gap-4">
              <div className="w-16 h-16 shrink-0 bg-gradient-to-br from-blue-400/20 to-blue-600/20 rounded-full flex items-center justify-center border border-blue-400/30">
                <svg className="w-8 h-8 text-blue-300" fill="currentColor" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14.2c-2.5 0-4.71-1.28-6-3.22.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09 6 3.08-1.29 1.94-3.5 3.22-6 3.22z"/></svg>
              </div>
              <div>
                <p className="text-lg font-bold">Need help finding the right part?</p>
                <p className="mt-1 text-[13px] text-slate-300 leading-relaxed">Our AI Assistant can help you match parts and check compatibility in seconds.</p>
                <button aria-label="Try AI Assistant" onClick={go('/chat')} className="mt-4 inline-flex items-center gap-2 rounded-lg bg-[#2563eb] hover:bg-[#1d4ed8] transition-colors px-4 py-2.5 text-[13px] font-semibold text-white border border-blue-500/50 shadow-md hover:shadow-lg">
                  <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2z"/></svg>
                  Try AI Assistant
                </button>
              </div>
            </div>
          </div>

          <div className="lg:w-[65%] grid sm:grid-cols-2 md:grid-cols-4 gap-3">
            {[
              ["Parts Compatibility", "Guaranteed fit for your vehicle.", "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z", "text-[#2563eb] bg-blue-50"],
              ["Multiple Conditions", "New, Used, OEM, Aftermarket options.", "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z", "text-[#2563eb] bg-blue-50"],
              ["Global Shipping", "We ship worldwide to most countries.", "M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9", "text-[#2563eb] bg-blue-50"],
              ["Easy Returns", "Hassle-free returns within 30 days.", "M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15", "text-[#2563eb] bg-blue-50"]
            ].map(([title, text, iconPath, colors]) => (
              <div key={title} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm flex flex-col hover:shadow-md transition-all">
                <div className={`w-10 h-10 rounded-full flex items-center justify-center mb-3 ${colors.split(" ")[1]}`}>
                  <svg className={`w-5 h-5 ${colors.split(" ")[0]}`} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={iconPath} /></svg>
                </div>
                <p className="font-bold text-[13px] text-[#021737] leading-tight">{title}</p>
                <p className="text-[11px] text-slate-500 mt-1">{text}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
