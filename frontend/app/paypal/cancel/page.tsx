"use client";

export default function PayPalCancel() {
  return (
    <main className="min-h-screen">
      <div className="mx-auto max-w-2xl px-6 py-20">
        <p className="mb-2 text-xs font-semibold uppercase tracking-[0.35em] text-amber-400">
          PayGuard AI
        </p>
        <h1 className="text-3xl font-bold text-white">Payment cancelled</h1>
        <p className="mt-4 text-slate-400">
          The buyer cancelled the PayPal approval. No money moved. You can start
          a new mission from the PayGuard dashboard.
        </p>

        <a
          href="/"
          className="mt-8 inline-block rounded-lg border border-slate-600 px-5 py-2.5 font-semibold text-slate-200 hover:border-slate-400"
        >
          Back to PayGuard dashboard
        </a>
      </div>
    </main>
  );
}
