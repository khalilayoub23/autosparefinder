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

export default function TrackOrderPage() {
  const [orderNumber, setOrderNumber] = useState("");
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const canSubmit = useMemo(() => {
    return orderNumber.trim().length >= 6 && email.trim().includes("@");
  }, [orderNumber, email]);

  const onSubmit = async (e) => {
    e.preventDefault();
    if (!canSubmit) return;

    setLoading(true);
    setError("");
    setResult(null);

    try {
      const params = new URLSearchParams({
        order_number: orderNumber.trim(),
        email: email.trim(),
      });
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
    <div className="min-h-screen bg-[#f4f7fd] text-slate-900">
      <Header />

      <main className="max-w-3xl mx-auto px-4 py-10">
        <div className="rounded-2xl border border-slate-200 bg-white shadow-sm p-6 sm:p-8">
          <h1 className="text-2xl sm:text-3xl font-black text-slate-900">Track Your Order</h1>
          <p className="mt-2 text-slate-600 text-sm sm:text-base">
            Enter your order number and the same email used at checkout.
          </p>

          <form className="mt-6 space-y-4" onSubmit={onSubmit}>
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-1">Order Number</label>
              <input
                value={orderNumber}
                onChange={(e) => setOrderNumber(e.target.value)}
                placeholder="e.g. ORD-20260517-1234"
                className="w-full rounded-lg border border-slate-300 px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-[#2563eb]/30 focus:border-[#2563eb]"
              />
            </div>

            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-1">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full rounded-lg border border-slate-300 px-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-[#2563eb]/30 focus:border-[#2563eb]"
              />
            </div>

            <button
              type="submit"
              disabled={!canSubmit || loading}
              className="inline-flex items-center justify-center rounded-lg bg-[#2563eb] hover:bg-[#1d4ed8] disabled:bg-slate-300 disabled:cursor-not-allowed transition-colors px-5 py-2.5 text-white font-semibold"
            >
              {loading ? "Checking..." : "Track Order"}
            </button>
          </form>

          {error && (
            <div className="mt-5 rounded-lg border border-red-200 bg-red-50 text-red-700 px-4 py-3 text-sm">
              {error}
            </div>
          )}

          {result && (
            <div className="mt-6 rounded-xl border border-slate-200 bg-slate-50 p-4 sm:p-5">
              <div className="text-sm text-slate-500">Order</div>
              <div className="text-lg font-bold text-slate-900">{result.order_number}</div>

              <div className="mt-3 text-sm text-slate-500">Status</div>
              <div className="text-base font-semibold text-slate-800">{statusLabel(result.status)}</div>

              {result.tracking_number && (
                <>
                  <div className="mt-3 text-sm text-slate-500">Tracking Number</div>
                  <div className="text-base font-mono text-slate-900">{result.tracking_number}</div>
                </>
              )}

              {result.estimated_delivery && (
                <>
                  <div className="mt-3 text-sm text-slate-500">Estimated Delivery</div>
                  <div className="text-base text-slate-800">{new Date(result.estimated_delivery).toLocaleString()}</div>
                </>
              )}

              {result.tracking_url && (
                <a
                  href={result.tracking_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-4 inline-flex items-center text-[#2563eb] hover:text-[#1d4ed8] font-semibold"
                >
                  Open Carrier Tracking
                </a>
              )}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
