"use client";

import type { Transaction } from "../lib/api";
import { payActionFor } from "../lib/payAction";

/**
 * The one PayPal checkout button used everywhere (Overview, Transactions,
 * drawer, Approval Center, Missions). Renders nothing unless the status allows
 * checkout. Clicking delegates to the page's existing payWithPayPal handler
 * (POST /execute -> redirect to PayPal's Sandbox approve_url in this tab).
 */
export function PayButton({
  txn,
  onPay,
  busy = false,
  size = "md",
}: {
  txn: Transaction;
  onPay: (t: Transaction) => void;
  busy?: boolean;
  size?: "sm" | "md";
}) {
  const action = payActionFor(txn.status);
  if (!action) return null;
  const pad = size === "sm" ? "px-2.5 py-1 text-xs" : "px-4 py-2 text-sm";
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation(); // don't also open the row's drawer
        onPay(txn);
      }}
      disabled={busy}
      aria-label={`${action.label} for transaction #${txn.id}`}
      className={`whitespace-nowrap rounded-lg bg-emerald-500 font-semibold text-slate-950 hover:bg-emerald-400 disabled:opacity-50 ${pad}`}
    >
      {busy ? "Opening PayPal…" : action.label}
    </button>
  );
}
