import React from "react";
import hero from "../assets/hero.svg";

const steps = [
  ["1", "Search", "Find parts by VIN, OEM, SKU or vehicle details."],
  ["2", "Compare", "Compare prices, condition, delivery & suppliers."],
  ["3", "Order or Quote", "Place an order or request a quote."],
  ["4", "Track & Receive", "Track your order and get support anytime."]
];

const categories = [
  ["Engine Parts", "25,000+ parts"],
  ["Brake System", "18,000+ parts"],
  ["Suspension", "12,000+ parts"],
  ["Electrical", "20,000+ parts"],
  ["Body Parts", "15,000+ parts"],
  ["Transmission", "8,000+ parts"],
  ["Cooling System", "7,000+ parts"],
  ["Filters", "6,000+ parts"],
  ["Exhaust", "9,000+ parts"]
];

export default function HowAndCategories() {
  return (
    <section className="bg-white">
      <div className="max-w-6xl mx-auto px-4 py-8">
        <div className="grid lg:grid-cols-[0.95fr_1.45fr] gap-6">
          <div>
            <h2 className="text-3xl font-extrabold text-[#0f274e]">How AUTOSPAREFINDER Works</h2>
            <div className="mt-5 space-y-5">
              {steps.map((step) => (
                <div key={step[0]} className="flex items-start gap-4">
                  <div className="h-9 w-9 rounded-full bg-brandBlue text-white flex items-center justify-center font-bold">{step[0]}</div>
                  <div>
                    <p className="font-semibold text-[#122b54]">{step[1]}</p>
                    <p className="text-sm text-slate-600">{step[2]}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-3xl font-extrabold text-[#122b54]">Top Categories</h3>
              <button aria-label="View all categories" className="text-brandBlue text-sm font-semibold">View All Categories</button>
            </div>

            <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {categories.map(([name, count], idx) => (
                <article key={name} className="perspective-800">
                  <div className="preserve-3d w-full rounded-2xl border border-slate-200 bg-white p-4 shadow-sm transition duration-300 hover:-translate-y-1">
                    <div className="mx-auto w-28 h-28 md:w-32 md:h-32 bg-white rounded-2xl shadow-[0_18px_40px_rgba(0,0,0,0.45)] transition duration-300 hover:rotate-0 hover:-translate-y-2 hover:scale-105 flex items-center justify-center overflow-hidden">
                      <img
                        loading="lazy"
                        src={hero}
                        alt={name + " illustration"}
                        className="w-full h-full object-cover"
                        style={{ objectPosition: `${(idx * 10) % 100}% center` }}
                      />
                    </div>
                    <p className="mt-3 text-center font-semibold text-[#132d59]">{name}</p>
                    <p className="text-center text-xs text-slate-500">{count}</p>
                  </div>
                </article>
              ))}
            </div>
          </div>
        </div>

        <div className="mt-8 grid lg:grid-cols-[1.2fr_2fr] gap-4">
          <div className="rounded-2xl p-5 bg-gradient-to-r from-[#052661] to-[#0a3f9a] text-white">
            <p className="text-2xl font-bold">Need help finding the right part?</p>
            <p className="mt-2 text-sm text-slate-200">Our AI Assistant can help you match parts and check compatibility in seconds.</p>
            <button aria-label="Try AI Assistant" className="mt-4 rounded-lg bg-brandBlue px-4 py-2 text-sm font-semibold">Try AI Assistant</button>
          </div>

          <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-3 text-sm">
            {[
              ["Parts Compatibility", "Guaranteed fit for your vehicle."],
              ["Multiple Conditions", "New, Used, OEM, Aftermarket options."],
              ["Global Shipping", "We ship worldwide to most countries."],
              ["Easy Returns", "Hassle-free returns within 30 days."]
            ].map(([title, text]) => (
              <div key={title} className="rounded-xl border border-slate-200 bg-white px-4 py-4">
                <p className="font-semibold text-[#142f5d]">{title}</p>
                <p className="text-xs text-slate-500 mt-1">{text}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
