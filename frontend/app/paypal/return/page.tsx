"use client";

import { useEffect, useRef, useState } from "react";
import { isSandboxCheckoutUrl, paypalReturn, type CheckoutResult } from "../../lib/api";
import { statusLabel } from "../../lib/ui";

// PayPal redirects here after the buyer approves in the Sandbox checkout, with
// ?token=<order id>&PayerID=<id>. We never assume success: the backend captures
// the order and verifies PayPal's actual response, and we show that result.

const TONE: Record<string, { title: string; color: string }> = {
  COMPLETED: { title: "Payment completed ✓", color: "text-emerald-400" },
  ALREADY_COMPLETED: { title: "Payment completed ✓", color: "text-emerald-400" },
  PENDING: { title: "Payment pending at PayPal", color: "text-amber-400" },
  NOT_APPROVED: { title: "Not approved at PayPal yet", color: "text-amber-400" },
  DECLINED_RETRY: { title: "Payment method declined", color: "text-amber-400" },
  RETRYABLE_ERROR: { title: "Couldn't confirm the payment yet", color: "text-amber-400" },
  FAILED: { title: "Payment failed", color: "text-rose-400" },
  NOT_CAPTURABLE: { title: "Nothing to capture", color: "text-slate-300" },
};

export default function PayPalReturn() {
  const [token, setToken] = useState<string | null>(null);
  const [payerId, setPayerId] = useState<string | null>(null);
  const [result, setResult] = useState<CheckoutResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const started = useRef(false); // capture once, even under React strict mode

  async function confirm(t: string, p: string | null) {
    setBusy(true);
    setError(null);
    try {
      setResult(await paypalReturn(t, p));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reach PayGuard.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const params = new URLSearchParams(window.location.search);
    const t = params.get("token");
    const p = params.get("PayerID");
    setToken(t);
    setPayerId(p);
    if (t) confirm(t, p);
    else setError("PayPal did not include an order token in the return link.");
  }, []);

  const tone = result ? TONE[result.outcome] ?? TONE.NOT_CAPTURABLE : null;
  const txn = result?.transaction;
  const canRetryCapture =
    !!token && !!result && ["RETRYABLE_ERROR", "NOT_APPROVED", "PENDING"].includes(result.outcome);
  const retryUrl = isSandboxCheckoutUrl(result?.retry_url) ? result?.retry_url : null;

  return (
    <main className="min-h-screen">
      <div className="mx-auto max-w-2xl px-6 py-20">
        <p className="mb-2 text-xs font-semibold uppercase tracking-[0.35em] text-emerald-400">
          PayGuard AI · PayPal Sandbox
        </p>

        {busy && !result && (
          <h1 className="text-3xl font-bold text-white" aria-live="polite">
            Confirming your payment with PayPal…
          </h1>
        )}
        {tone && (
          <h1 className={`text-3xl font-bold ${tone.color}`} aria-live="polite">
            {tone.title}
          </h1>
        )}
        {result && <p className="mt-4 text-slate-300">{result.message}</p>}
        {error && (
          <p className="mt-4 text-rose-400" role="alert">
            {error}
          </p>
        )}

        <div className="mt-6 rounded-xl border border-slate-800 bg-slate-900/60 p-5 text-sm">
          {txn && (
            <>
              <p className="text-slate-400">PayGuard transaction</p>
              <p className="font-mono text-slate-200">
                #{txn.id} · {statusLabel(txn.status)}
              </p>
            </>
          )}
          <p className="mt-3 text-slate-400">PayPal order</p>
          <p className="font-mono text-emerald-300">{txn?.paypal_order_id ?? token ?? "—"}</p>
          <p className="mt-3 text-slate-400">PayPal capture</p>
          <p className="font-mono text-slate-200">{txn?.paypal_capture_id ?? "—"}</p>
          <p className="mt-3 text-slate-400">Payer ID</p>
          <p className="font-mono text-slate-200">{payerId ?? "—"}</p>
        </div>

        <div className="mt-8 flex flex-wrap gap-3">
          {retryUrl && (
            <a
              href={retryUrl}
              className="rounded-lg bg-emerald-500 px-5 py-2.5 font-semibold text-slate-950 hover:bg-emerald-400"
            >
              Return to PayPal
            </a>
          )}
          {(canRetryCapture || (error && token)) && (
            <button
              onClick={() => token && confirm(token, payerId)}
              disabled={busy}
              className="rounded-lg border border-slate-600 px-5 py-2.5 font-semibold text-slate-200 hover:border-slate-400 disabled:opacity-50"
            >
              {busy ? "Checking…" : "Check again"}
            </button>
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
