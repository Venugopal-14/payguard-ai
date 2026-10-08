"use client";

import { useEffect, useMemo, useState } from "react";
import {
  approve,
  capture,
  createMission,
  execute,
  health,
  listTransactions,
  type ProposeResponse,
  type Transaction,
} from "./lib/api";

const DECISION_STYLES: Record<string, string> = {
  APPROVE: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  NEEDS_APPROVAL: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  BLOCK: "bg-rose-500/15 text-rose-300 border-rose-500/40",
};

const RISK_STYLES: Record<string, string> = {
  LOW: "text-emerald-300",
  MEDIUM: "text-amber-300",
  HIGH: "text-rose-300",
};

const STATUS_BADGE: Record<string, string> = {
  COMPLETED: "bg-emerald-500/15 text-emerald-300",
  APPROVED: "bg-sky-500/15 text-sky-300",
  AWAITING_APPROVAL: "bg-amber-500/15 text-amber-300",
  PAYPAL_CREATED: "bg-sky-500/15 text-sky-300",
  BLOCKED: "bg-rose-500/15 text-rose-300",
  FAILED: "bg-rose-500/15 text-rose-300",
};

export default function Home() {
  const [instruction, setInstruction] = useState(
    "Buy a mechanical keyboard from a trusted store"
  );
  const [budget, setBudget] = useState(150);
  const [threshold, setThreshold] = useState(200);
  const currency = "USD";

  const [result, setResult] = useState<ProposeResponse | null>(null);
  const [txn, setTxn] = useState<Transaction | null>(null);
  const [history, setHistory] = useState<Transaction[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cfg, setCfg] = useState<{
    paypal: boolean;
    ai: boolean;
    provider: string;
  } | null>(null);
  const [detail, setDetail] = useState<Transaction | null>(null);

  async function refreshHistory() {
    try {
      setHistory(await listTransactions());
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    health()
      .then((h) =>
        setCfg({
          paypal: h.paypal_configured,
          ai: h.ai_configured,
          provider: h.ai_provider,
        })
      )
      .catch(() => setCfg(null));
    refreshHistory();
  }, []);

  const metrics = useMemo(() => {
    const total = history.length;
    const approved = history.filter((t) =>
      ["APPROVED", "PAYPAL_CREATED", "COMPLETED"].includes(t.status)
    ).length;
    const blocked = history.filter((t) => t.status === "BLOCKED").length;
    const awaiting = history.filter(
      (t) => t.status === "AWAITING_APPROVAL"
    ).length;
    return { total, approved, blocked, awaiting };
  }, [history]);

  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
      return undefined;
    } finally {
      setBusy(false);
      refreshHistory();
    }
  }

  async function onPropose() {
    const r = await run(() =>
      createMission({ instruction, budget, currency, approval_threshold: threshold })
    );
    if (r) {
      setResult(r);
      setTxn(r.transaction);
    }
  }

  async function onApprove() {
    if (!txn) return;
    const t = await run(() => approve(txn.id));
    if (t) setTxn(t);
  }
  async function onExecute() {
    if (!txn) return;
    const t = await run(() => execute(txn.id));
    if (t) {
      setTxn(t);
      if (t.approve_url) window.open(t.approve_url, "_blank");
    }
  }
  async function onCapture() {
    if (!txn) return;
    const t = await run(() => capture(txn.id));
    if (t) setTxn(t);
  }

  const decision = txn?.decision ?? result?.transaction.decision;

  // Pipeline stage states
  const stages = (() => {
    const hasProposal = !!result;
    const riskDone = hasProposal;
    const policyDone = hasProposal;
    const st = txn?.status;
    const paid = st === "COMPLETED";
    return [
      { label: "AI Proposal", done: hasProposal },
      { label: "Risk Analysis", done: riskDone },
      { label: "Policy Evaluation", done: policyDone },
      {
        label: paid
          ? "Payment Complete"
          : decision === "BLOCK"
          ? "Blocked"
          : "Payment",
        done: paid,
        warn: decision === "NEEDS_APPROVAL" && !paid,
        block: decision === "BLOCK",
      },
    ];
  })();

  return (
    <main className="min-h-screen">
      <div className="mx-auto max-w-6xl px-6 py-10">
        {/* Header */}
        <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="mb-1 text-xs font-semibold uppercase tracking-[0.35em] text-emerald-400">
              PayGuard AI
            </p>
            <h1 className="text-3xl font-bold tracking-tight text-white sm:text-4xl">
              AI Payment Authorization & Security Control Plane
            </h1>
            <p className="mt-3 max-w-2xl text-sm text-slate-400">
              The AI proposes. PayGuard&apos;s deterministic risk and policy
              engines decide. PayPal only executes what PayGuard authorizes.
            </p>
          </div>
          {cfg && (
            <div className="flex flex-col gap-2 text-xs">
              <StatusDot
                ok={cfg.ai}
                label={
                  cfg.ai
                    ? `AI: Agent connected (${cfg.provider})`
                    : "AI: Fallback mode"
                }
              />
              <StatusDot
                ok={cfg.paypal}
                label={cfg.paypal ? "PayPal Sandbox connected" : "PayPal not set"}
              />
              <StatusDot ok label="Policy Engine active" />
            </div>
          )}
        </header>

        {/* Metrics */}
        <div className="mb-8 grid grid-cols-2 gap-4 lg:grid-cols-4">
          <Metric label="Transactions" value={metrics.total} />
          <Metric label="Approved" value={metrics.approved} tone="emerald" />
          <Metric label="Blocked" value={metrics.blocked} tone="rose" />
          <Metric label="Awaiting approval" value={metrics.awaiting} tone="amber" />
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          {/* Mission */}
          <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
            <h2 className="mb-1 text-lg font-semibold text-white">
              Give your AI agent a mission
            </h2>
            <p className="mb-4 text-xs text-slate-500">
              PayGuard analyzes the request before any payment can occur.
            </p>

            <label className="mb-1 block text-sm text-slate-400">Instruction</label>
            <textarea
              className="mb-4 h-24 w-full rounded-lg border border-slate-700 bg-slate-950 p-3 text-sm outline-none focus:border-emerald-500"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
            />
            <div className="mb-4 grid grid-cols-2 gap-4">
              <div>
                <label className="mb-1 block text-sm text-slate-400">
                  Budget ({currency})
                </label>
                <input
                  type="number"
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 p-2 text-sm outline-none focus:border-emerald-500"
                  value={budget}
                  onChange={(e) => setBudget(Number(e.target.value))}
                />
              </div>
              <div>
                <label className="mb-1 block text-sm text-slate-400">
                  Approval threshold
                </label>
                <input
                  type="number"
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 p-2 text-sm outline-none focus:border-emerald-500"
                  value={threshold}
                  onChange={(e) => setThreshold(Number(e.target.value))}
                />
              </div>
            </div>
            <button
              onClick={onPropose}
              disabled={busy}
              className="w-full rounded-lg bg-emerald-500 px-4 py-2.5 font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:opacity-50"
            >
              {busy ? "Analyzing…" : "Analyze mission"}
            </button>
            {error && (
              <p className="mt-4 rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-300">
                {error}
              </p>
            )}
          </section>

          {/* AI analysis + decision */}
          <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
            <h2 className="mb-4 text-lg font-semibold text-white">
              AI analysis & decision
            </h2>

            {!result && (
              <p className="text-sm text-slate-500">
                Submit a mission to see the agent proposal, confidence, risk
                score, and the policy decision.
              </p>
            )}

            {result && (
              <div className="space-y-4">
                {/* Agent card */}
                <div className="rounded-xl border border-slate-800 bg-slate-950 p-4">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-xs font-semibold uppercase tracking-wider text-emerald-400">
                      🧠 Agent ·{" "}
                      {result.agent.source === "llm"
                        ? `${result.agent.provider}`
                        : "fallback"}
                    </span>
                    <span className="text-xs text-slate-500">
                      confidence {(result.agent.confidence * 100).toFixed(0)}%
                    </span>
                  </div>
                  <p className="font-medium text-white">
                    {result.transaction.description}
                  </p>
                  <p className="text-sm text-slate-400">
                    {result.transaction.merchant} ·{" "}
                    {result.transaction.amount.toFixed(2)}{" "}
                    {result.transaction.currency} · {result.agent.category}
                  </p>
                  {result.agent.summary && (
                    <p className="mt-2 text-sm text-slate-400">
                      {result.agent.summary}
                    </p>
                  )}
                </div>

                {/* Pipeline */}
                <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-950 px-4 py-3">
                  {stages.map((s, i) => (
                    <div key={i} className="flex items-center">
                      <div className="flex flex-col items-center">
                        <span
                          className={`flex h-6 w-6 items-center justify-center rounded-full text-xs ${
                            s.block
                              ? "bg-rose-500/20 text-rose-300"
                              : s.warn
                              ? "bg-amber-500/20 text-amber-300"
                              : s.done
                              ? "bg-emerald-500/20 text-emerald-300"
                              : "bg-slate-700/40 text-slate-500"
                          }`}
                        >
                          {s.block ? "✕" : s.warn ? "!" : s.done ? "✓" : "○"}
                        </span>
                        <span className="mt-1 w-16 text-center text-[10px] text-slate-500">
                          {s.label}
                        </span>
                      </div>
                      {i < stages.length - 1 && (
                        <span className="mx-1 h-px w-6 bg-slate-700" />
                      )}
                    </div>
                  ))}
                </div>

                {/* Risk + policy */}
                <div className="grid grid-cols-2 gap-3">
                  <div className="rounded-xl border border-slate-800 bg-slate-950 p-4">
                    <p className="text-xs text-slate-400">Risk</p>
                    <p
                      className={`text-xl font-bold ${
                        RISK_STYLES[result.risk.level] ?? ""
                      }`}
                    >
                      {result.risk.score}
                      <span className="text-sm font-normal text-slate-500">
                        /100
                      </span>
                    </p>
                    <p
                      className={`text-xs font-semibold ${
                        RISK_STYLES[result.risk.level] ?? ""
                      }`}
                    >
                      {result.risk.level}
                    </p>
                  </div>
                  <div className="rounded-xl border border-slate-800 bg-slate-950 p-4">
                    <p className="text-xs text-slate-400">Policy decision</p>
                    {decision && (
                      <span
                        className={`mt-1 inline-block rounded-full border px-2.5 py-0.5 text-xs font-semibold ${
                          DECISION_STYLES[decision] ?? ""
                        }`}
                      >
                        {decision.replace("_", " ")}
                      </span>
                    )}
                    <p className="mt-2 text-[11px] leading-snug text-slate-500">
                      {result.policy_reasons[0]}
                    </p>
                  </div>
                </div>

                <details className="rounded-xl border border-slate-800 bg-slate-950 p-4 text-sm">
                  <summary className="cursor-pointer text-slate-300">
                    Full reasoning
                  </summary>
                  <pre className="mt-3 whitespace-pre-wrap text-xs text-slate-400">
                    {txn?.reasoning ?? result.transaction.reasoning}
                  </pre>
                </details>

                {/* Actions */}
                <div className="flex flex-wrap gap-3">
                  {txn?.status === "AWAITING_APPROVAL" && (
                    <button
                      onClick={onApprove}
                      disabled={busy}
                      className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-amber-400 disabled:opacity-50"
                    >
                      Approve as human
                    </button>
                  )}
                  {txn?.status === "APPROVED" && (
                    <button
                      onClick={onExecute}
                      disabled={busy}
                      className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-emerald-400 disabled:opacity-50"
                    >
                      Create PayPal order
                    </button>
                  )}
                  {txn?.status === "PAYPAL_CREATED" && (
                    <>
                      {txn.approve_url && (
                        <a
                          href={txn.approve_url}
                          target="_blank"
                          className="rounded-lg border border-slate-600 px-4 py-2 text-sm font-semibold text-slate-200 hover:border-slate-400"
                        >
                          Open PayPal approval ↗
                        </a>
                      )}
                      <button
                        onClick={onCapture}
                        disabled={busy}
                        className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-emerald-400 disabled:opacity-50"
                      >
                        Capture payment
                      </button>
                    </>
                  )}
                  {txn?.status === "COMPLETED" && (
                    <span className="rounded-lg bg-emerald-500/15 px-4 py-2 text-sm font-semibold text-emerald-300">
                      ✓ Completed · capture {txn.paypal_capture_id}
                    </span>
                  )}
                  {txn?.status === "BLOCKED" && (
                    <span className="rounded-lg bg-rose-500/15 px-4 py-2 text-sm font-semibold text-rose-300">
                      Blocked by policy — no PayPal order created.
                    </span>
                  )}
                </div>
              </div>
            )}
          </section>
        </div>

        {/* Transaction explorer */}
        <section className="mt-8 rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
          <h2 className="mb-4 text-lg font-semibold text-white">
            Transaction explorer
          </h2>
          {history.length === 0 ? (
            <p className="text-sm text-slate-500">No transactions yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-slate-500">
                  <tr>
                    <th className="py-2 pr-4">#</th>
                    <th className="py-2 pr-4">Item</th>
                    <th className="py-2 pr-4">Merchant</th>
                    <th className="py-2 pr-4">Amount</th>
                    <th className="py-2 pr-4">Risk</th>
                    <th className="py-2 pr-4">Decision</th>
                    <th className="py-2 pr-4">Status</th>
                  </tr>
                </thead>
                <tbody className="text-slate-300">
                  {history.map((t) => (
                    <tr
                      key={t.id}
                      onClick={() => setDetail(t)}
                      className="cursor-pointer border-t border-slate-800 hover:bg-slate-800/40"
                    >
                      <td className="py-2 pr-4">{t.id}</td>
                      <td className="py-2 pr-4">{t.description}</td>
                      <td className="py-2 pr-4">{t.merchant}</td>
                      <td className="py-2 pr-4">
                        {Number(t.amount).toFixed(2)} {t.currency}
                      </td>
                      <td
                        className={`py-2 pr-4 font-medium ${
                          RISK_STYLES[t.risk_level] ?? ""
                        }`}
                      >
                        {t.risk_level}
                      </td>
                      <td className="py-2 pr-4">{t.decision.replace("_", " ")}</td>
                      <td className="py-2 pr-4">
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                            STATUS_BADGE[t.status] ?? "bg-slate-700/40 text-slate-300"
                          }`}
                        >
                          {t.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      {/* Detail drawer */}
      {detail && (
        <div
          className="fixed inset-0 z-40 bg-black/50"
          onClick={() => setDetail(null)}
        >
          <aside
            className="absolute right-0 top-0 h-full w-full max-w-md overflow-y-auto border-l border-slate-800 bg-slate-900 p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-lg font-semibold text-white">
                Transaction #{detail.id}
              </h3>
              <button
                onClick={() => setDetail(null)}
                className="text-slate-400 hover:text-white"
              >
                ✕
              </button>
            </div>
            <p className="font-medium text-white">{detail.description}</p>
            <p className="text-sm text-slate-400">
              {detail.merchant} · {Number(detail.amount).toFixed(2)}{" "}
              {detail.currency}
            </p>

            <Divider />
            <Row label="Risk" value={`${detail.risk_score} · ${detail.risk_level}`} />
            <Row label="Decision" value={detail.decision.replace("_", " ")} />
            <Row label="Status" value={detail.status} />
            <Divider />
            <Row label="PayPal order" value={detail.paypal_order_id ?? "—"} />
            <Row label="PayPal capture" value={detail.paypal_capture_id ?? "—"} />
            <Divider />
            <p className="mb-1 text-xs text-slate-500">Reasoning</p>
            <pre className="whitespace-pre-wrap text-xs text-slate-400">
              {detail.reasoning}
            </pre>
          </aside>
        </div>
      )}
    </main>
  );
}

function StatusDot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="flex items-center gap-2 text-slate-300">
      <span
        className={`h-2 w-2 rounded-full ${
          ok ? "bg-emerald-400" : "bg-slate-500"
        }`}
      />
      {label}
    </span>
  );
}

function Metric({
  label,
  value,
  tone = "slate",
}: {
  label: string;
  value: number;
  tone?: "slate" | "emerald" | "rose" | "amber";
}) {
  const toneClass = {
    slate: "text-white",
    emerald: "text-emerald-300",
    rose: "text-rose-300",
    amber: "text-amber-300",
  }[tone];
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
      <p className="text-xs uppercase tracking-wider text-slate-500">{label}</p>
      <p className={`mt-2 text-3xl font-bold ${toneClass}`}>{value}</p>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between py-1.5 text-sm">
      <span className="text-slate-500">{label}</span>
      <span className="font-mono text-slate-200">{value}</span>
    </div>
  );
}

function Divider() {
  return <div className="my-4 h-px bg-slate-800" />;
}
