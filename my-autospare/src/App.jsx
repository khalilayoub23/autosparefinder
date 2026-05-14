import React from "react";
import Header from "./components/Header";
import Hero from "./components/Hero";
import HowAndCategories from "./components/HowAndCategories";

export default function App() {
  return (
    <div className="min-h-screen bg-[#f4f7fd] text-slate-900">
      <Header />
      <Hero />
      <HowAndCategories />
    </div>
  );
}
