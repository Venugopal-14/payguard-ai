"use client";

import { useEffect, useMemo, useState } from "react";
import {
  approve,
  capture,
  createMission,
  execute,
  redirectToPayPal,
  fraudAnalytics,
  health,
  listTransactions,
  type FraudAnalytics,
  type ProposeResponse,
  type Transaction,
} from "./lib/api";
import {
  DECISION_BADGE,
  DECISION_TEXT,
  RISK_BADGE,
  STATUS_BADGE,
  RISK_TEXT,
  computeMetrics,
  fmtMoney,
  fmtTime,
  parseReasoning,
  recommendationLabel,
  statusLabel,
} from "./lib/ui";
import { Badge, Card, RiskGauge, StatusDot } from "./components/primitives";
import { TransactionDrawer } from "./components/TransactionDrawer";
import { PayButton } from "./components/PayButton";
import { canStartPayPalCheckout } from "./lib/payAction";
import { OrchestratorView } from "./components/OrchestratorView";

type View =
  | "Overview"
  | "Mission Orchestrator"
  | "Missions"
  | "Approval Center"
  | "Transactions"
  | "Fraud Prevention"
  | "Risk Analytics"
  | "Policy Control"
  | "Audit Trail";

const NAV: { key: View; icon: string }[] = [
  { key: "Overview", icon: "▦" },
  { key: "Mission Orchestrator", icon: "⛓" },
  { key: "Missions", icon: "✦" },
  { key: "Approval Center", icon: "◉" },
  { key: "Transactions", icon: "≣" },
  { key: "Fraud Prevention", icon: "🛡" },
  { key: "Risk Analytics", icon: "◈" },
  { key: "Policy Control", icon: "⚙" },
  { key: "Audit Trail", icon: "❖" },
];

export default function Home() {
  const [view, setView] = useState<View>("Overview");
  const [history, setHistory] = useState<Transaction[]>([]);
  const [cfg, setCfg] = useState<{ paypal: boolean; ai: boolean; provider: string } | null>(null);
  const [detail, setDetail] = useState<Transaction | null>(null);

  // Mission workspace state
  const [instruction, setInstruction] = useState("Buy a mechanical keyboard from a trusted store");
  const [budget, setBudget] = useState(150);
  const [threshold, setThreshold] = useState(200);
  const [result, setResult] = useState<ProposeResponse | null>(null);
  const [txn, setTxn] = useState<Transaction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Connection state for the dashboard's data load. null = healthy.
  const [connErr, setConnErr] = useState<string | null>(null);

  // Fetch transactions. Returns true on success, false on failure, so callers
  // can decide whether to retry / surface an error instead of silently showing
  // misleading zero data.
  async function refresh(): Promise<boolean> {
    try {
      setHistory(await listTransactions());
      return true;
    } catch {
      return false;
    }
  }

  useEffect(() => {
    let cancelled = false;

    // health is best-effort (drives status dots only); the transaction fetch is
    // what determines whether we show data or a connection error.
    async function loadConfig() {
      try {
        const h = await health();
        if (!cancelled) setCfg({ paypal: h.paypal_configured, ai: h.ai_configured, provider: h.ai_provider });
      } catch {
        if (!cancelled) setCfg(null);
      }
    }

    (async () => {
      await loadConfig();
      const ok = await refresh();
      if (cancelled || ok) return;

      // First load failed (e.g. Render free-tier cold start). Surface a clear
      // "connecting" message, wait, and retry exactly once.
      setConnErr("Connecting to backend…");
      await new Promise((r) => setTimeout(r, 4000));
      if (cancelled) return;
      await loadConfig();
      const retryOk = await refresh();
      if (cancelled) return;
      setConnErr(
        retryOk
          ? null
          : "Can't reach the backend. It may be waking up — click Retry in a moment."
      );
    })();

    return () => { cancelled = true; };
  }, []);

  // Manual retry from the banner: only clear the error on a confirmed success.
  async function retryConnection() {
    setConnErr("Connecting to backend…");
    const ok = await refresh();
    setConnErr(ok ? null : "Still can't reach the backend. Please try again shortly.");
  }

  const metrics = useMemo(() => computeMetrics(history), [history]);
  const awaiting = useMemo(
    () => history.filter((t) => t.status === "AWAITING_APPROVAL"),
    [history]
  );

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
      refresh();
    }
  }

  async function onPropose() {
    const r = await run(() => createMission({ instruction, budget, currency: "USD", approval_threshold: threshold }));
    if (r) { setResult(r); setTxn(r.transaction); }
  }
  async function onApprove(id: number) {
    const t = await run(() => approve(id));
    if (t && txn && t.id === txn.id) setTxn(t);
  }
  // Human approval must already be done (status APPROVED). This creates the
  // PayPal Sandbox order (or reuses the existing one) and redirects this tab to
  // the real PayPal-hosted checkout. PayPal sends the buyer back to /paypal/return.
  async function payWithPayPal(target: Transaction) {
    // Defense in depth: the button is only rendered for these statuses, and
    // the backend also refuses, but never call /execute or redirect otherwise.
    if (!canStartPayPalCheckout(target.status)) {
      setError(`Transaction #${target.id} is ${target.status}; PayPal checkout is not available.`);
      return;
    }
    const t = await run(async () => {
      const r = await execute(target.id);
      redirectToPayPal(r.approve_url); // same tab, Sandbox host only
      return r;
    });
    if (t && txn && t.id === txn.id) setTxn(t);
  }
  async function onExecute() {
    if (txn) await payWithPayPal(txn);
  }
  async function onCapture() {
    if (!txn) return;
    const t = await run(() => capture(txn.id));
    if (t) setTxn(t);
  }

  return (
    <div className="flex min-h-screen bg-[#060b16] text-slate-200">
      {/* Sidebar */}
      <aside className="fixed left-0 top-0 z-30 flex h-full w-60 flex-col border-r border-slate-800/80 bg-[#0a0f1c] px-4 py-5">
        <div className="mb-8 flex items-center gap-2 px-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-emerald-400 to-sky-500 text-sm font-bold text-slate-950">P</div>
          <div>
            <p className="text-sm font-bold tracking-tight text-white">PayGuard AI</p>
            <p className="text-[10px] text-slate-500">Secure AI Payments</p>
          </div>
        </div>

        <nav className="flex-1 space-y-1">
          {NAV.map((n) => (
            <button
              key={n.key}
              onClick={() => setView(n.key)}
              className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm transition ${
                view === n.key
                  ? "bg-slate-800/70 font-medium text-white"
                  : "text-slate-400 hover:bg-slate-800/40 hover:text-slate-200"
              }`}
            >
              <span className="flex items-center gap-3">
                <span className="text-slate-500">{n.icon}</span>
                {n.key}
              </span>
              {n.key === "Approval Center" && awaiting.length > 0 && (
                <span className="rounded-full bg-amber-500/20 px-1.5 text-[10px] font-bold text-amber-400">
                  {awaiting.length}
                </span>
              )}
            </button>
          ))}
        </nav>

        <div className="mt-4 space-y-2 rounded-xl border border-slate-800/80 bg-slate-900/40 p-3">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">System status</p>
          <StatusDot ok={!!cfg} label="Backend online" />
          <StatusDot ok={!!cfg?.ai} label={cfg?.ai ? `AI agent (${cfg.provider})` : "AI fallback"} />
          <StatusDot ok={!!cfg?.paypal} label="PayPal Sandbox" />
          <StatusDot ok label="Policy engine" />
        </div>
      </aside>

      {/* Main */}
      <main className="ml-60 flex-1">
        {/* Top bar */}
        <header className="sticky top-0 z-20 flex items-center justify-between border-b border-slate-800/80 bg-[#060b16]/90 px-8 py-4 backdrop-blur">
          <div>
            <h1 className="text-lg font-semibold text-white">{view}</h1>
            <p className="text-xs text-slate-500">AI Payment Authorization & Security Control Plane</p>
          </div>
          <div className="flex items-center gap-4">
            <span className="rounded-full border border-slate-700 px-3 py-1 text-xs text-slate-400">Sandbox</span>
            <div className="flex gap-4">
              <StatusDot ok={!!cfg?.ai} label="AI" />
              <StatusDot ok={!!cfg?.paypal} label="PayPal" />
              <StatusDot ok label="Policy" />
            </div>
          </div>
        </header>

        <div className="px-8 py-6">
          {connErr && (
            <div className="mb-4 flex items-center gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-300">
              <span aria-hidden>⚠</span>
              <span>{connErr}</span>
              <button
                onClick={retryConnection}
                className="ml-auto rounded-lg border border-amber-500/40 px-3 py-1 text-xs font-semibold text-amber-200 hover:bg-amber-500/20"
              >
                Retry
              </button>
            </div>
          )}
          {/* Action errors (e.g. PayPal checkout) for views without their own error slot. */}
          {error && view !== "Missions" && !detail && (
            <div role="alert" className="mb-4 rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-sm text-rose-300">
              {error}
            </div>
          )}
          {view === "Overview" && (
            <OverviewView
              metrics={metrics}
              history={history}
              onOpen={setDetail}
              onPay={payWithPayPal}
              busy={busy}
              goMissions={() => setView("Missions")}
            />
          )}
          {view === "Missions" && (
            <MissionsView
              instruction={instruction} setInstruction={setInstruction}
              budget={budget} setBudget={setBudget}
              threshold={threshold} setThreshold={setThreshold}
              busy={busy} error={error} result={result} txn={txn}
              onPropose={onPropose} onApprove={() => txn && onApprove(txn.id)}
              onExecute={onExecute} onCapture={onCapture}
            />
          )}
          {view === "Mission Orchestrator" && (
            <OrchestratorView
              onCreated={() => { refresh(); }}
              goApproval={() => setView("Approval Center")}
            />
          )}
          {view === "Approval Center" && (
            <ApprovalCenter
              items={awaiting}
              ready={history.filter((t) => canStartPayPalCheckout(t.status))}
              busy={busy}
              onApprove={onApprove}
              onOpen={setDetail}
              onPay={payWithPayPal}
            />
          )}
          {view === "Transactions" && (
            <TransactionsView history={history} onOpen={setDetail} onPay={payWithPayPal} busy={busy} />
          )}
          {view === "Fraud Prevention" && (
            <FraudPrevention history={history} onOpen={setDetail} />
          )}
          {view === "Risk Analytics" && <RiskAnalytics history={history} />}
          {view === "Policy Control" && <PolicyControl threshold={threshold} />}
          {view === "Audit Trail" && <AuditTrail history={history} onOpen={setDetail} />}
        </div>
      </main>

      {detail && (
        <TransactionDrawer
          txn={detail}
          onClose={() => setDetail(null)}
          busy={busy}
          error={error}
          onPay={payWithPayPal}
        />
      )}
    </div>
  );
}

/* ---------- Overview ---------- */
function OverviewView({ metrics, history, onOpen, onPay, busy, goMissions }: {
  metrics: ReturnType<typeof computeMetrics>;
  history: Transaction[];
  onOpen: (t: Transaction) => void;
  onPay: (t: Transaction) => void;
  busy: boolean;
  goMissions: () => void;
}) {
  const dist = { LOW: 0, MEDIUM: 0, HIGH: 0, CRITICAL: 0 } as Record<string, number>;
  history.forEach((t) => { dist[t.risk_level] = (dist[t.risk_level] ?? 0) + 1; });
  const maxDist = Math.max(1, ...Object.values(dist));

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Kpi label="Volume (completed)" value={fmtMoney(metrics.volume)} accent="text-white" />
        <Kpi label="Transactions" value={String(metrics.total)} accent="text-white" />
        <Kpi label="Blocked" value={String(metrics.blocked)} accent="text-rose-400" />
        <Kpi label="Awaiting approval" value={String(metrics.awaiting)} accent="text-amber-400" />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="p-6 lg:col-span-2">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="font-semibold text-white">Recent decisions</h3>
            <button onClick={goMissions} className="rounded-lg bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-emerald-400">
              + New mission
            </button>
          </div>
          <TxTable rows={history.slice(0, 6)} onOpen={onOpen} onPay={onPay} busy={busy} compact />
        </Card>

        <Card className="p-6">
          <h3 className="mb-4 font-semibold text-white">Risk distribution</h3>
          <div className="space-y-3">
            {(["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const).map((lvl) => (
              <div key={lvl}>
                <div className="mb-1 flex justify-between text-xs">
                  <span className={RISK_TEXT[lvl]}>{lvl}</span>
                  <span className="text-slate-500">{dist[lvl] ?? 0}</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-slate-800">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${((dist[lvl] ?? 0) / maxDist) * 100}%`,
                      background: lvl === "LOW" ? "#34d399" : lvl === "MEDIUM" ? "#fbbf24" : "#fb7185",
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}

/* ---------- Missions ---------- */
function MissionsView(props: {
  instruction: string; setInstruction: (v: string) => void;
  budget: number; setBudget: (v: number) => void;
  threshold: number; setThreshold: (v: number) => void;
  busy: boolean; error: string | null;
  result: ProposeResponse | null; txn: Transaction | null;
  onPropose: () => void; onApprove: () => void; onExecute: () => void; onCapture: () => void;
}) {
  const { result, txn } = props;
  const decision = txn?.decision ?? result?.transaction.decision;
  const stages = [
    { label: "AI Proposal", done: !!result },
    { label: "Risk", done: !!result },
    { label: "Policy", done: !!result },
    {
      label: txn?.status === "COMPLETED" ? "Paid" : decision === "BLOCK" ? "Blocked" : "Payment",
      done: txn?.status === "COMPLETED",
      // Waiting on a human, regardless of the policy recommendation.
      warn: txn?.status === "AWAITING_APPROVAL",
      block: decision === "BLOCK",
    },
  ];

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card className="p-6">
        <h3 className="font-semibold text-white">Give your AI agent a mission</h3>
        <p className="mb-4 text-xs text-slate-500">PayGuard analyzes the request before any payment can occur.</p>
        <label className="mb-1 block text-sm text-slate-400">Instruction</label>
        <textarea
          className="mb-4 h-24 w-full rounded-lg border border-slate-700 bg-slate-950 p-3 text-sm outline-none focus:border-emerald-500"
          value={props.instruction} onChange={(e) => props.setInstruction(e.target.value)}
        />
        <div className="mb-4 grid grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-sm text-slate-400">Budget (USD)</label>
            <input type="number" className="w-full rounded-lg border border-slate-700 bg-slate-950 p-2 text-sm outline-none focus:border-emerald-500"
              value={props.budget} onChange={(e) => props.setBudget(Number(e.target.value))} />
          </div>
          <div>
            <label className="mb-1 block text-sm text-slate-400">Approval threshold</label>
            <input type="number" className="w-full rounded-lg border border-slate-700 bg-slate-950 p-2 text-sm outline-none focus:border-emerald-500"
              value={props.threshold} onChange={(e) => props.setThreshold(Number(e.target.value))} />
          </div>
        </div>
        <button onClick={props.onPropose} disabled={props.busy}
          className="w-full rounded-lg bg-emerald-500 px-4 py-2.5 font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:opacity-50">
          {props.busy ? "Analyzing…" : "Analyze mission"}
        </button>
        {props.error && (
          <p className="mt-4 rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-300">{props.error}</p>
        )}
      </Card>

      <Card className="p-6">
        <h3 className="mb-4 font-semibold text-white">AI analysis & decision</h3>
        {!result && <p className="text-sm text-slate-500">Submit a mission to see the agent proposal, risk gauge, and policy decision.</p>}
        {result && (
          <div className="space-y-4">
            {/* pipeline */}
            <div className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-950 px-4 py-3">
              {stages.map((s, i) => (
                <div key={i} className="flex items-center">
                  <div className="flex flex-col items-center">
                    <span className={`flex h-6 w-6 items-center justify-center rounded-full text-xs ${
                      s.block ? "bg-rose-500/20 text-rose-300" : s.warn ? "bg-amber-500/20 text-amber-300" : s.done ? "bg-emerald-500/20 text-emerald-300" : "bg-slate-700/40 text-slate-500"
                    }`}>{s.block ? "✕" : s.warn ? "!" : s.done ? "✓" : "○"}</span>
                    <span className="mt-1 w-14 text-center text-[9px] text-slate-500">{s.label}</span>
                  </div>
                  {i < stages.length - 1 && <span className="mx-1 h-px w-5 bg-slate-700" />}
                </div>
              ))}
            </div>

            {/* agent */}
            <div className="rounded-xl border border-slate-800 bg-slate-950 p-4">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-xs font-semibold uppercase tracking-wider text-emerald-400">
                  Agent · {result.agent.source === "llm" ? result.agent.provider : "fallback"}
                </span>
                <span className="text-xs text-slate-500">confidence {(result.agent.confidence * 100).toFixed(0)}%</span>
              </div>
              <p className="font-medium text-white">{result.transaction.description}</p>
              <p className="text-sm text-slate-400">{result.transaction.merchant} · {fmtMoney(result.transaction.amount, result.transaction.currency)} · {result.agent.category}</p>
            </div>

            {/* risk gauge + factors */}
            <div className="flex gap-5 rounded-xl border border-slate-800 bg-slate-950 p-4">
              <RiskGauge score={result.risk.score} level={result.risk.level} />
              <div className="flex-1">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">Risk factors</p>
                <ul className="space-y-1">
                  {result.risk.factors.map((f, i) => (
                    <li key={i} className="flex items-center justify-between gap-2 text-xs">
                      <span className="text-slate-300">{f.description}</span>
                      <span className="shrink-0 rounded bg-slate-800 px-1.5 py-0.5 font-mono text-slate-400">+{f.weight}</span>
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-[11px] text-slate-500">→ Recommended: {result.risk.recommended_action.replace("_", " ")}</p>
              </div>
            </div>

            {/* policy */}
            <div className="rounded-xl border border-slate-800 bg-slate-950 p-4">
              <div className="mb-2 flex items-center gap-2">
                <span className="text-xs text-slate-400">Policy recommendation</span>
                {decision && <Badge label={decision} className={DECISION_BADGE[decision] ?? ""} />}
                {txn && <Badge label={statusLabel(txn.status)} className={STATUS_BADGE[txn.status] ?? "border-slate-700 text-slate-300"} />}
              </div>
              <p className="text-xs text-slate-500">{result.policy_reasons[0]?.replace("Auto-approved.", "Meets policy criteria for approval.")}</p>
              {txn?.status === "AWAITING_APPROVAL" && (
                <p className="mt-1 text-xs text-amber-300">Pending approval: a human must approve before any PayPal order is created.</p>
              )}
            </div>

            {/* actions */}
            <div className="flex flex-wrap gap-3">
              {txn?.status === "AWAITING_APPROVAL" && (
                <button onClick={props.onApprove} disabled={props.busy} className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-amber-400 disabled:opacity-50">Approve as human</button>
              )}
              {txn && <PayButton txn={txn} onPay={() => props.onExecute()} busy={props.busy} />}
              {txn?.status === "PAYPAL_CREATED" && (
                <button onClick={props.onCapture} disabled={props.busy} className="rounded-lg border border-slate-600 px-4 py-2 text-sm font-semibold text-slate-200 hover:border-slate-400 disabled:opacity-50" title="Fallback if the automatic capture after returning from PayPal did not complete">Retry capture</button>
              )}
              {txn?.status === "COMPLETED" && <span className="rounded-lg bg-emerald-500/15 px-4 py-2 text-sm font-semibold text-emerald-300">✓ Completed · {txn.paypal_capture_id}</span>}
              {txn?.status === "BLOCKED" && <span className="rounded-lg bg-rose-500/15 px-4 py-2 text-sm font-semibold text-rose-300">Blocked by policy — no PayPal order.</span>}
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}

/* ---------- Approval Center ---------- */
function ApprovalCenter({ items, ready, busy, onApprove, onOpen, onPay }: {
  items: Transaction[]; ready: Transaction[]; busy: boolean;
  onApprove: (id: number) => void; onOpen: (t: Transaction) => void; onPay: (t: Transaction) => void;
}) {
  // Human-approved transactions waiting for PayPal checkout (APPROVED /
  // PAYPAL_CREATED). Approving above moves an item here after refresh.
  const readySection = ready.length > 0 && (
    <Card className="p-5">
      <h3 className="font-semibold text-white">Approved — ready for PayPal checkout</h3>
      <p className="mb-3 text-xs text-slate-500">
        Human approval is done. Checkout happens on PayPal&apos;s Sandbox page as a Sandbox buyer.
      </p>
      <div className="space-y-2">
        {ready.map((t) => (
          <div key={t.id} onClick={() => onOpen(t)}
            className="flex cursor-pointer items-center justify-between gap-3 rounded-lg border border-slate-800/60 px-4 py-2.5 text-sm hover:bg-slate-800/40">
            <span className="text-slate-300">
              #{t.id} {t.description} · {fmtMoney(Number(t.amount), t.currency)}
            </span>
            <div className="flex items-center gap-2">
              <Badge label={statusLabel(t.status)} className={STATUS_BADGE[t.status] ?? "border-slate-700 text-slate-300"} />
              <PayButton txn={t} onPay={onPay} busy={busy} size="sm" />
            </div>
          </div>
        ))}
      </div>
    </Card>
  );

  if (items.length === 0)
    return (
      <div className="space-y-5">
        <Card className="p-12 text-center">
          <p className="text-4xl">✓</p>
          <p className="mt-3 font-semibold text-white">All clear</p>
          <p className="mt-1 text-sm text-slate-500">No approvals require your attention right now.</p>
        </Card>
        {readySection}
      </div>
    );
  return (
    <div className="space-y-5">
      {readySection}
      <p className="text-sm text-slate-400">
        <span className="font-semibold text-amber-400">{items.length}</span> action{items.length > 1 ? "s" : ""} require your attention
      </p>
      {items.map((t) => {
        const parsed = parseReasoning(t.reasoning);
        return (
          <Card key={t.id} className="overflow-hidden">
            <div className="flex items-start justify-between gap-4 border-b border-slate-800/60 p-5">
              <div>
                <p className="text-xs uppercase tracking-wider text-slate-500">Transaction #{t.id}</p>
                <p className="mt-1 text-2xl font-bold text-white">{fmtMoney(Number(t.amount), t.currency)}</p>
                <p className="text-sm text-slate-400">{t.description} · {t.merchant}</p>
              </div>
              <div className="text-right">
                <Badge label={t.risk_level} className={RISK_BADGE[t.risk_level] ?? ""} />
                <p className={`mt-2 text-2xl font-bold ${RISK_TEXT[t.risk_level] ?? ""}`}>{Math.round(Number(t.risk_score))}<span className="text-xs text-slate-500">/100</span></p>
              </div>
            </div>

            <div className="grid gap-5 p-5 md:grid-cols-2">
              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">Risk signals</p>
                <ul className="space-y-1">
                  {parsed.risk.length ? parsed.risk.map((s, i) => (
                    <li key={i} className="text-sm text-slate-300">• {s}</li>
                  )) : <li className="text-sm text-slate-500">No risk signals recorded.</li>}
                </ul>
              </div>
              <div>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">Policy</p>
                <ul className="space-y-1">
                  {parsed.policy.length ? parsed.policy.map((s, i) => (
                    <li key={i} className="text-sm text-slate-300">• {s}</li>
                  )) : <li className="text-sm text-slate-500">Human decision required.</li>}
                </ul>
                <p className="mt-3 text-xs text-slate-500">
                  Policy recommendation:{" "}
                  <span className={`font-semibold ${DECISION_TEXT[t.decision] ?? "text-slate-300"}`}>{t.decision.replace(/_/g, " ")}</span>
                  {" "}· Human approval: <span className="font-semibold text-amber-400">PENDING</span>
                </p>
              </div>
            </div>

            <div className="flex justify-end gap-2 border-t border-slate-800/60 p-4">
              <button onClick={() => onOpen(t)} className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-semibold text-slate-300 hover:border-slate-500">Investigate</button>
              <button onClick={() => onApprove(t.id)} disabled={busy}
                className="rounded-lg bg-emerald-500 px-5 py-2 text-sm font-semibold text-slate-950 hover:bg-emerald-400 disabled:opacity-50">
                {busy ? "Working…" : "Approve"}
              </button>
            </div>
          </Card>
        );
      })}
    </div>
  );
}

/* ---------- Transactions ---------- */
function TransactionsView({ history, onOpen, onPay, busy }: {
  history: Transaction[]; onOpen: (t: Transaction) => void; onPay: (t: Transaction) => void; busy: boolean;
}) {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("ALL");
  const [risk, setRisk] = useState("ALL");
  const rows = history.filter((t) => {
    if (q && !`${t.description} ${t.merchant}`.toLowerCase().includes(q.toLowerCase())) return false;
    if (status !== "ALL" && t.status !== status) return false;
    if (risk !== "ALL" && t.risk_level !== risk) return false;
    return true;
  });
  return (
    <Card className="p-6">
      <div className="mb-4 flex flex-wrap gap-3">
        <input placeholder="Search item or merchant…" value={q} onChange={(e) => setQ(e.target.value)}
          className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-emerald-500" />
        <Select value={status} onChange={setStatus} options={["ALL", "COMPLETED", "APPROVED", "AWAITING_APPROVAL", "PAYPAL_CREATED", "BLOCKED", "FAILED"]} />
        <Select value={risk} onChange={setRisk} options={["ALL", "LOW", "MEDIUM", "HIGH", "CRITICAL"]} />
      </div>
      <TxTable rows={rows} onOpen={onOpen} onPay={onPay} busy={busy} />
    </Card>
  );
}

/* ---------- Risk Analytics ---------- */
function RiskAnalytics({ history }: { history: Transaction[] }) {
  const m = computeMetrics(history);
  const dist = { LOW: 0, MEDIUM: 0, HIGH: 0, CRITICAL: 0 } as Record<string, number>;
  history.forEach((t) => { dist[t.risk_level] = (dist[t.risk_level] ?? 0) + 1; });
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Kpi label="Human-approved" value={String(m.approved)} accent="text-emerald-400" />
        <Kpi label="Blocked" value={String(m.blocked)} accent="text-rose-400" />
        <Kpi label="Pending approval" value={String(m.awaiting)} accent="text-amber-400" />
        <Kpi label="Completed" value={String(m.completed)} accent="text-emerald-400" />
      </div>
      <Card className="p-6">
        <h3 className="mb-4 font-semibold text-white">Risk level frequency</h3>
        <div className="space-y-3">
          {(["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const).map((lvl) => {
            const max = Math.max(1, ...Object.values(dist));
            return (
              <div key={lvl}>
                <div className="mb-1 flex justify-between text-xs"><span className={RISK_TEXT[lvl]}>{lvl}</span><span className="text-slate-500">{dist[lvl] ?? 0}</span></div>
                <div className="h-2.5 overflow-hidden rounded-full bg-slate-800">
                  <div className="h-full rounded-full" style={{ width: `${((dist[lvl] ?? 0) / max) * 100}%`, background: lvl === "LOW" ? "#34d399" : lvl === "MEDIUM" ? "#fbbf24" : "#fb7185" }} />
                </div>
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}

/* ---------- Policy Control (read-only) ---------- */
function PolicyControl({ threshold }: { threshold: number }) {
  return (
    <Card className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="font-semibold text-white">Policy configuration</h3>
        <span className="rounded-full border border-slate-700 px-2.5 py-0.5 text-[10px] text-slate-500">Read-only · editing coming later</span>
      </div>
      <div className="space-y-1">
        <PolicyRow label="Approval threshold (per mission)" value={fmtMoney(threshold)} />
        <PolicyRow label="Auto-block" value="Over budget · HIGH/CRITICAL risk" />
        <PolicyRow label="Blocked merchants" value="silkroad, darkmarket, unknownvendor" />
        <PolicyRow label="Blocked categories" value="gambling, weapons, adult" />
        <PolicyRow label="Risk rule" value="MEDIUM → human approval" />
        <PolicyRow label="Human approval" value="Required for every non-blocked payment" />
      </div>
    </Card>
  );
}

/* ---------- Audit Trail ---------- */
function AuditTrail({ history, onOpen }: { history: Transaction[]; onOpen: (t: Transaction) => void }) {
  return (
    <Card className="p-6">
      <h3 className="mb-4 font-semibold text-white">Audit trail</h3>
      <div className="space-y-2">
        {history.map((t) => (
          <div key={t.id} onClick={() => onOpen(t)}
            className="flex cursor-pointer items-center justify-between rounded-lg border border-slate-800/60 px-4 py-2.5 text-sm hover:bg-slate-800/40">
            <div className="flex items-center gap-3">
              <span className="font-mono text-xs text-slate-500">{fmtTime(t.created_at)}</span>
              <span className="text-slate-300">#{t.id} {t.description}</span>
            </div>
            <div className="flex items-center gap-2">
              <Badge label={recommendationLabel(t.decision)} className={DECISION_BADGE[t.decision] ?? "border-slate-700 text-slate-300"} />
              <Badge label={statusLabel(t.status)} className={STATUS_BADGE[t.status] ?? "border-slate-700 text-slate-300"} />
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

/* ---------- Fraud Prevention ---------- */
function FraudPrevention({
  history,
  onOpen,
}: {
  history: Transaction[];
  onOpen: (t: Transaction) => void;
}) {
  const [data, setData] = useState<FraudAnalytics | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fraudAnalytics()
      .then(setData)
      .catch((e) => setErr(e instanceof Error ? e.message : "Failed to load"));
  }, []);

  if (err)
    return (
      <Card className="p-6">
        <p className="text-sm text-rose-300">{err}</p>
      </Card>
    );
  if (!data)
    return (
      <Card className="p-6">
        <p className="text-sm text-slate-500">Loading analytics…</p>
      </Card>
    );

  const money = (m: Record<string, number>) => {
    const keys = Object.keys(m);
    if (keys.length === 0) return "0.00";
    // Currency-safe: list each currency separately, never summed together.
    return keys.map((k) => fmtMoney(m[k], k)).join(" · ");
  };

  const blocked = history.filter((t) => t.status === "BLOCKED");

  return (
    <div className="space-y-6">
      {/* Simulated banner */}
      <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-2 text-xs text-amber-300">
        All fraud-prevention figures are <strong>simulated</strong> from the
        risk/policy engine — not real-world money saved. Completed payments are
        excluded.
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Kpi
          label="Simulated fraud attempts blocked"
          value={String(data.simulated_fraud_attempts_blocked)}
          accent="text-rose-300"
        />
        <Kpi
          label="Simulated blocked amount"
          value={money(data.simulated_blocked_amount_by_currency)}
          accent="text-rose-300"
        />
        <Kpi
          label="Approval rate"
          value={
            data.approval_rate_pct === null
              ? "—"
              : `${data.approval_rate_pct}%`
          }
          accent="text-emerald-300"
        />
        <Kpi
          label="Pending approvals"
          value={String(data.pending_approval)}
          accent="text-amber-300"
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Risk distribution */}
        <Card className="p-6">
          <h3 className="mb-4 font-semibold text-white">Risk distribution</h3>
          <div className="space-y-3">
            {(["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const).map((lvl) => {
              const counts = Object.values(data.risk_distribution);
              const max = Math.max(1, ...counts);
              const v = data.risk_distribution[lvl] ?? 0;
              return (
                <div key={lvl}>
                  <div className="mb-1 flex justify-between text-xs">
                    <span className={RISK_TEXT[lvl]}>{lvl}</span>
                    <span className="text-slate-500">{v}</span>
                  </div>
                  <div className="h-2.5 overflow-hidden rounded-full bg-slate-800">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${(v / max) * 100}%`,
                        background:
                          lvl === "LOW"
                            ? "#34d399"
                            : lvl === "MEDIUM"
                            ? "#fbbf24"
                            : "#fb7185",
                      }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </Card>

        {/* Decision breakdown + policy blocked */}
        <Card className="p-6">
          <h3 className="mb-1 font-semibold text-white">Policy recommendation breakdown</h3>
          <p className="mb-4 text-xs text-slate-500">Engine recommendations, not human approvals.</p>
          <div className="space-y-2 text-sm">
            <Line label="Policy: approve" value={data.decision_breakdown.APPROVE ?? 0} tone="text-emerald-300" />
            <Line label="Policy: needs approval" value={data.decision_breakdown.NEEDS_APPROVAL ?? 0} tone="text-amber-300" />
            <Line label="Policy: block" value={data.decision_breakdown.BLOCK ?? 0} tone="text-rose-300" />
          </div>
          <div className="mt-4 border-t border-slate-800 pt-4 text-sm">
            <p className="text-xs text-slate-500">
              Policy-blocked amount (budget/limit — not fraud)
            </p>
            <p className="mt-1 text-slate-200">
              {money(data.policy_blocked_amount_by_currency)}
            </p>
          </div>
        </Card>
      </div>

      {/* Blocked transactions with reasons */}
      <Card className="p-6">
        <h3 className="mb-4 font-semibold text-white">
          Blocked transactions &amp; reasons
        </h3>
        {blocked.length === 0 ? (
          <p className="text-sm text-slate-500">
            No blocked transactions yet.
          </p>
        ) : (
          <div className="space-y-2">
            {blocked.map((t) => (
              <div
                key={t.id}
                onClick={() => onOpen(t)}
                className="cursor-pointer rounded-lg border border-slate-800/60 p-3 hover:bg-slate-800/40"
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm text-slate-200">
                    #{t.id} {t.description} · {t.merchant}
                  </span>
                  <div className="flex items-center gap-2">
                    <Badge
                      label={t.risk_level}
                      className={RISK_BADGE[t.risk_level] ?? ""}
                    />
                    <span className="text-sm text-slate-400">
                      {fmtMoney(Number(t.amount), t.currency)}
                    </span>
                  </div>
                </div>
                <p className="mt-1 text-xs text-slate-500">
                  {t.reasoning
                    .split("\n")
                    .filter((l) => l.startsWith("Policy:"))
                    .map((l) => l.replace("Policy: ", ""))
                    .join(" ") || "Blocked by policy."}
                </p>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function Line({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-slate-400">{label}</span>
      <span className={`font-semibold ${tone}`}>{value}</span>
    </div>
  );
}

/* ---------- shared bits ---------- */
function Kpi({ label, value, accent }: { label: string; value: string; accent: string }) {
  return (
    <Card className="p-5">
      <p className="text-xs uppercase tracking-wider text-slate-500">{label}</p>
      <p className={`mt-2 text-3xl font-bold ${accent}`}>{value}</p>
    </Card>
  );
}

function Select({ value, onChange, options }: { value: string; onChange: (v: string) => void; options: string[] }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}
      className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-300 outline-none focus:border-emerald-500">
      {options.map((o) => <option key={o} value={o}>{statusLabel(o)}</option>)}
    </select>
  );
}

function PolicyRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-slate-800/60 py-2.5 text-sm">
      <span className="text-slate-400">{label}</span>
      <span className="text-right text-slate-200">{value}</span>
    </div>
  );
}

function TxTable({ rows, onOpen, onPay, busy = false, compact }: {
  rows: Transaction[]; onOpen: (t: Transaction) => void;
  onPay?: (t: Transaction) => void; busy?: boolean; compact?: boolean;
}) {
  if (rows.length === 0) return <p className="text-sm text-slate-500">No transactions.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wider text-slate-500">
          <tr className="border-b border-slate-800">
            <th className="py-2 pr-4">#</th>
            <th className="py-2 pr-4">Item</th>
            {!compact && <th className="py-2 pr-4">Merchant</th>}
            <th className="py-2 pr-4">Amount</th>
            <th className="py-2 pr-4">Risk</th>
            <th className="py-2 pr-4">Policy recommendation</th>
            <th className="py-2 pr-4">Status</th>
            {onPay && <th className="py-2 pr-4"><span className="sr-only">Action</span></th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.id} onClick={() => onOpen(t)}
              className="cursor-pointer border-b border-slate-800/50 hover:bg-slate-800/40">
              <td className="py-2.5 pr-4 text-slate-500">{t.id}</td>
              <td className="py-2.5 pr-4 text-slate-200">{t.description}</td>
              {!compact && <td className="py-2.5 pr-4 text-slate-400">{t.merchant}</td>}
              <td className="py-2.5 pr-4 text-slate-200">{fmtMoney(Number(t.amount), t.currency)}</td>
              <td className={`py-2.5 pr-4 font-medium ${RISK_TEXT[t.risk_level] ?? ""}`}>{t.risk_level}</td>
              <td className="py-2.5 pr-4 text-slate-300">{t.decision.replace(/_/g, " ")}</td>
              <td className="py-2.5 pr-4">
                <Badge label={statusLabel(t.status)} className={STATUS_BADGE[t.status] ?? "border-slate-700 text-slate-300"} />
              </td>
              {onPay && (
                <td className="py-2.5 pr-4 text-right">
                  <PayButton txn={t} onPay={onPay} busy={busy} size="sm" />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
