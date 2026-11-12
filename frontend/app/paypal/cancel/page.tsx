"use client";

import { useEffect, useRef, useState } from "react";
import { isSandboxCheckoutUrl, paypalCancel, type CheckoutResult } from "../../lib/api";

// PayPal redirects here (?token=<order id>) when the buyer cancels checkout.
// We record the cancellation; the transaction stays PAYPAL_CREATED so the
// buyer can retry with the same Sandbox order. No money moves.

export default function PayPalCancel() {
  const [result, setResult] = useState<CheckoutResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const token = new URLSearchParams(window.location.search).get("token");
    if (!token) return;
    paypalCancel(token)
      .then(setResult)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not reach PayGuard."));
  }, []);

  const retryUrl = isSandboxCheckoutUrl(result?.retry_url) ? result?.retry_url : null;

  return (
    <main className="min-h-screen">
      <div className="mx-auto max-w-2xl px-6 py-20">
        <p className="mb-2 text-xs font-semibold uppercase tracking-[0.35em] text-amber-400">
          PayGuard AI · PayPal Sandbox
        </p>
        <h1 className="text-3xl font-bold text-white">Checkout cancelled</h1>
        <p className="mt-4 text-slate-400">
          {result?.message ??
            "The buyer cancelled the PayPal checkout. No money moved."}
        </p>
        {result?.transaction && (
          <p className="mt-2 text-sm text-slate-500">
            Transaction #{result.transaction.id} is{" "}
            {result.transaction.status.replace(/_/g, " ")}.
          </p>
        )}
        {error && (
          <p className="mt-4 text-sm text-rose-400" role="alert">
            {error}
          </p>
        )}

        <div className="mt-8 flex flex-wrap gap-3">
          {retryUrl && (
            <a
              href={retryUrl}
              className="rounded-lg bg-emerald-500 px-5 py-2.5 font-semibold text-slate-950 hover:bg-emerald-400"
            >
              Retry checkout
            </a>
          )}
          <a
            href="/"
            className="rounded-lg border border-slate-600 px-5 py-2.5 font-semibold text-slate-200 hover:border-slate-400"
          >
            Back to PayGuard dashboard
          </a>
        </div>
      </div>
    </main>
  );
}
