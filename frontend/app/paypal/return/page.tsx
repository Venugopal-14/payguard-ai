"use client";

import { useEffect, useState } from "react";

export default function PayPalReturn() {
  const [token, setToken] = useState<string | null>(null);
  const [payerId, setPayerId] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setToken(params.get("token"));
    setPayerId(params.get("PayerID"));
  }, []);

  return (
    <main className="min-h-screen">
      <div className="mx-auto max-w-2xl px-6 py-20">
        <p className="mb-2 text-xs font-semibold uppercase tracking-[0.35em] text-emerald-400">
          PayGuard AI
        </p>
        <h1 className="text-3xl font-bold text-white">
          Payment approved in PayPal ✓
        </h1>
        <p className="mt-4 text-slate-400">
          The buyer approved this order in the PayPal Sandbox. Go back to the
          PayGuard dashboard and click <strong>Capture payment</strong> to
          complete the transaction.
        </p>

        <div className="mt-6 rounded-xl border border-slate-800 bg-slate-900/60 p-5 text-sm">
          <p className="text-slate-400">PayPal order token</p>
          <p className="font-mono text-emerald-300">{token ?? "—"}</p>
          <p className="mt-3 text-slate-400">Payer ID</p>
          <p className="font-mono text-slate-200">{payerId ?? "—"}</p>
        </div>

        <a
          href="/"
          className="mt-8 inline-block rounded-lg bg-emerald-500 px-5 py-2.5 font-semibold text-slate-950 hover:bg-emerald-400"
        >
          Back to PayGuard dashboard
        </a>
      </div>
    </main>
  );
}
