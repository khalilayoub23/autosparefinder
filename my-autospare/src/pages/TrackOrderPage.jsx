import React, { useMemo, useState } from "react";
import Header from "../components/Header";

function statusLabel(status) {
  const map = {
    pending_payment: "Pending Payment",
    paid: "Paid",
    confirmed: "Confirmed",
    processing: "Processing",
    supplier_ordered: "Ordered From Supplier",
    shipped: "Shipped",
    delivered: "Delivered",
    cancelled: "Cancelled",
    refunded: "Refunded",
  };
  return map[status] || status || "Unknown";
}

function formatDate(value) {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleString();
}

export default function TrackOrderPage() {
  const [orderNumber, setOrderNumber] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const canSubmit = useMemo(() => orderNumber.trim().length >= 6, [orderNumber]);

  const onSubmit = async (e) => {
    e.preventDefault();
    if (!canSubmit) return;

    setLoading(true);
    setError("");
    setResult(null);

    try {
      const params = new URLSearchParams({ order_number: orderNumber.trim() });
      const res = await fetch(`/api/v1/orders/track-public?${params.toString()}`);
      const data = await res.json();
      if (!res.ok) {
        setError(typeof data?.detail === "string" ? data.detail : "Unable to find this order.");
      } else {
        setResult(data);
      }
    } catch {
      setError("Network error while checking order status.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#f8f9fa] text-[#212529]">
      <Header />

      <section className="bg-gradient-to-r from-[#0a1628] to-[#1a2f5a] text-white">
        <div className="max-w-[1280px] mx-auto px-6 py-10">
          <h1 className="text-[36px] md:text-[48px] leading-tight font-bold">Track Your Order</h1>
          <p className="mt-2 text-[14px] md:text-[16px] font-normal text-white/85">
            Find your latest delivery status quickly using your order number.
          </p>
        </div>
      </section>

      <main className="max-w-[1280px] mx-auto px-6 py-8">
        <div className="bg-white border border-[#e9ecef] rounded-xl p-6 md:p-8 shadow-sm">
          <h2 className="text-[24px] md:text-[28px] leading-tight font-bold text-[#212529]">Order Tracking</h2>

          <form onSubmit={onSubmit} className="mt-5 grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3 items-end">
            <div>
              <label className="block text-[12px] font-medium text-[#6c757d] mb-1">Order Number</label>
              <input
                value={orderNumber}
                onChange={(e) => setOrderNumber(e.target.value)}
                placeholder="e.g. ORD-20260517-1234"
                className="w-full rounded-lg border border-[#e9ecef] px-3 py-2.5 text-[14px] md:text-[16px] font-normal text-[#212529] focus:outline-none focus:ring-2 focus:ring-[#1e6ff0]/30 focus:border-[#1e6ff0]"
              />
            </div>

            <button
              type="submit"
              disabled={!canSubmit || loading}
              className="h-[42px] rounded-lg bg-[#1e6ff0] hover:bg-[#3d8ef0] disabled:bg-slate-300 disabled:cursor-not-allowed transition-colors px-5 text-white text-[14px] md:text-[16px] font-semibold"
            >
              {loading ? "Checking..." : "Track Order"}
            </button>
          </form>

          {error && (
            <div className="mt-5 rounded-lg border border-red-200 bg-red-50 text-red-700 px-4 py-3 text-[14px] font-normal">
              {error}
            </div>
          )}

          {result && (
            <div className="mt-6 border border-[#e9ecef] rounded-xl overflow-hidden">
              <div className="bg-[#f8f9fa] border-b border-[#e9ecef] px-4 py-3">
                <div className="text-[12px] font-medium text-[#6c757d]">Order</div>
                <div className="text-[16px] font-semibold text-[#212529]">{result.order_number}</div>
              </div>

              <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <div className="text-[12px] font-medium text-[#6c757d]">Status</div>
                  <div className="text-[14px] md:text-[16px] font-normal text-[#212529]">{statusLabel(result.status)}</div>
                </div>
                <div>
                  <div className="text-[12px] font-medium text-[#6c757d]">Tracking Number</div>
                  <div className="text-[14px] md:text-[16px] font-normal font-mono text-[#212529]">{result.tracking_number || "-"}</div>
                </div>
                <div>
                  <div className="text-[12px] font-medium text-[#6c757d]">Estimated Delivery</div>
                  <div className="text-[14px] md:text-[16px] font-normal text-[#212529]">{formatDate(result.estimated_delivery)}</div>
                </div>
                <div className="flex items-end">
                  {result.tracking_url ? (
                    <a
                      href={result.tracking_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center text-[14px] md:text-[16px] font-semibold text-[#1e6ff0] hover:text-[#3d8ef0]"
                    >
                      Open Carrier Tracking
                    </a>
                  ) : (
                    <span className="text-[14px] font-normal text-[#6c757d]">Carrier link not available yet</span>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
