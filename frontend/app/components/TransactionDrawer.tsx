"use client";

import { useState } from "react";
import type { Transaction } from "../lib/api";
import {
  DECISION_BADGE,
  RISK_BADGE,
  STATUS_BADGE,
  fmtMoney,
  fmtTime,
  parseReasoning,
} from "../lib/ui";
import { Badge, RiskGauge } from "./primitives";

const TABS = ["Overview", "Risk", "Policy", "AI Analysis", "Audit"] as const;
type Tab = (typeof TABS)[number];

export function TransactionDrawer({
  txn,
  onClose,
}: {
  txn: Transaction;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>("Overview");
  const parsed = parseReasoning(txn.reasoning);

  return (
    <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <aside
        className="absolute right-0 top-0 flex h-full w-full max-w-lg flex-col border-l border-slate-800 bg-slate-950 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="border-b border-slate-800 p-6">
          <div className="mb-3 flex items-start justify-between">
            <div>
              <p className="text-xs uppercase tracking-wider text-slate-500">
                Transaction #{txn.id}
              </p>
              <p className="mt-1 text-2xl font-bold text-white">
                {fmtMoney(Number(txn.amount), txn.currency)}
              </p>
              <p className="text-sm text-slate-400">{txn.merchant}</p>
            </div>
            <button
              onClick={onClose}
              className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-white"
            >
              ✕
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge
              label={txn.status}
              className={STATUS_BADGE[txn.status] ?? "border-slate-700 text-slate-300"}
            />
            <Badge
              label={txn.decision}
              className={DECISION_BADGE[txn.decision] ?? "border-slate-700 text-slate-300"}
            />
            <Badge
              label={txn.risk_level}
              className={RISK_BADGE[txn.risk_level] ?? "border-slate-700 text-slate-300"}
            />
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 border-b border-slate-800 px-4">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`border-b-2 px-3 py-3 text-sm font-medium transition ${
                tab === t
                  ? "border-emerald-400 text-white"
                  : "border-transparent text-slate-500 hover:text-slate-300"
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {tab === "Overview" && (
            <div className="space-y-1">
              <Row label="Item" value={txn.description} />
              <Row label="Merchant" value={txn.merchant} />
              <Row label="Amount" value={fmtMoney(Number(txn.amount), txn.currency)} />
              <Row label="Currency" value={txn.currency} />
              <Row label="Status" value={txn.status} />
              <Row label="Created" value={fmtTime(txn.created_at)} />
              <Row label="PayPal order" value={txn.paypal_order_id ?? "—"} mono />
              <Row label="PayPal capture" value={txn.paypal_capture_id ?? "—"} mono />
            </div>
          )}

          {tab === "Risk" && (
            <div>
              <div className="mb-4 flex items-center gap-5">
                <RiskGauge score={Number(txn.risk_score)} level={txn.risk_level} />
                <div>
                  <p className="text-xs text-slate-500">Risk level</p>
                  <p className="text-lg font-semibold text-white">{txn.risk_level}</p>
                </div>
              </div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
                Risk signals
              </p>
              <ul className="space-y-1.5">
                {parsed.risk.length ? (
                  parsed.risk.map((s, i) => (
                    <li key={i} className="text-sm text-slate-300">• {s}</li>
                  ))
                ) : (
                  <li className="text-sm text-slate-500">No risk signals recorded.</li>
                )}
              </ul>
            </div>
          )}

          {tab === "Policy" && (
            <div>
              <p className="mb-3">
                <span className="text-xs text-slate-500">Final decision: </span>
                <Badge
                  label={txn.decision}
                  className={DECISION_BADGE[txn.decision] ?? "border-slate-700 text-slate-300"}
                />
              </p>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
                Policy checks
              </p>
              <ul className="space-y-1.5">
                {parsed.policy.length ? (
                  parsed.policy.map((s, i) => (
                    <li key={i} className="text-sm text-slate-300">• {s}</li>
                  ))
                ) : (
                  <li className="text-sm text-slate-500">No policy notes recorded.</li>
                )}
              </ul>
            </div>
          )}

          {tab === "AI Analysis" && (
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
                Agent reasoning
              </p>
              <ul className="space-y-1.5">
                {parsed.agent.length ? (
                  parsed.agent.map((s, i) => (
                    <li key={i} className="text-sm text-slate-300">{s}</li>
                  ))
                ) : (
                  <li className="text-sm text-slate-500">No agent notes recorded.</li>
                )}
              </ul>
            </div>
          )}

          {tab === "Audit" && (
            <div className="space-y-3">
              {buildTimeline(txn).map((ev, i) => (
                <div key={i} className="flex gap-3">
                  <div className="flex flex-col items-center">
                    <span className="h-2 w-2 rounded-full bg-emerald-400" />
                    {i < buildTimeline(txn).length - 1 && (
                      <span className="h-full w-px bg-slate-700" />
                    )}
                  </div>
                  <div className="pb-3">
                    <p className="text-sm text-slate-200">{ev}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4 border-b border-slate-800/60 py-2 text-sm">
      <span className="text-slate-500">{label}</span>
      <span className={`text-right text-slate-200 ${mono ? "font-mono text-xs" : ""}`}>
        {value}
      </span>
    </div>
  );
}

function buildTimeline(txn: Transaction): string[] {
  const ev = ["Mission received", "AI proposal generated", "Risk evaluated", "Policy evaluated"];
  if (txn.decision === "BLOCK") ev.push("Blocked by policy");
  if (txn.status === "AWAITING_APPROVAL") ev.push("Human approval requested");
  if (["APPROVED", "PAYPAL_CREATED", "COMPLETED"].includes(txn.status))
    ev.push("Approved");
  if (["PAYPAL_CREATED", "COMPLETED"].includes(txn.status))
    ev.push("PayPal order created");
  if (txn.status === "COMPLETED") ev.push("Payment captured", "Transaction completed");
  if (txn.status === "FAILED") ev.push("Transaction failed");
  return ev;
}
