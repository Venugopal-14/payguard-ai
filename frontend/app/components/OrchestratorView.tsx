"use client";

import { useState } from "react";
import {
  createMission,
  orchestrate,
  type AgentStep,
  type OrchestrationResult,
  type Transaction,
} from "../lib/api";
import { Badge, Card } from "./primitives";
import { statusLabel } from "../lib/ui";

const STEP_BADGE: Record<string, string> = {
  ok: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  flagged: "bg-amber-500/10 text-amber-400 border-amber-500/30",
  blocked: "bg-rose-500/10 text-rose-400 border-rose-500/30",
  incomplete: "bg-amber-500/10 text-amber-400 border-amber-500/30",
  skipped: "bg-slate-500/10 text-slate-400 border-slate-500/30",
};

const DECISION_BADGE: Record<string, string> = {
  APPROVE: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  NEEDS_APPROVAL: "bg-amber-500/10 text-amber-400 border-amber-500/30",
  BLOCK: "bg-rose-500/10 text-rose-400 border-rose-500/30",
  NEEDS_INFO: "bg-slate-500/10 text-slate-300 border-slate-500/30",
};

const AGENT_ICON: Record<string, string> = {
  Planner: "🗺",
  Merchant: "🏪",
  Risk: "📊",
  Policy: "⚖",
  Supervisor: "🧭",
};

export function OrchestratorView({
  onCreated,
  goApproval,
}: {
  onCreated: () => void;
  goApproval: () => void;
}) {
  const [instruction, setInstruction] = useState(
    "Buy a keyboard under $50 from a trusted merchant"
  );
  // Budget is an OPTIONAL manual override; empty means "use the mission text".
  const [budget, setBudget] = useState<string>("");
  // Kept as text so typing never produces values like "02".
  const [threshold, setThreshold] = useState<string>("200");
  const [busy, setBusy] = useState(false);

  const detected = detectBudget(instruction);
  const manualRaw = Number(budget);
  const manual = budget.trim() !== "" && Number.isFinite(manualRaw) && manualRaw > 0 ? manualRaw : undefined;
  const thresholdRaw = Number(threshold);
  const thresholdNum = threshold.trim() !== "" && Number.isFinite(thresholdRaw) && thresholdRaw >= 0 ? thresholdRaw : 200;
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<OrchestrationResult | null>(null);
  const [created, setCreated] = useState<Transaction | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    setCreated(null);
    try {
      setResult(
        await orchestrate({
          instruction,
          budget: manual,
          currency: "USD",
          approval_threshold: thresholdNum,
        })
      );
    } catch (e) {
      setResult(null);
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  // Hands the mission to the EXISTING control-plane flow, using the SAME budget
  // and approval threshold the dry run evaluated, so the stored policy
  // recommendation matches what the Supervisor showed. The backend always
  // creates non-blocked transactions as AWAITING_APPROVAL (Pending approval),
  // so a human must approve before any PayPal order can be created.
  async function submitForApproval() {
    if (!result) return;
    const constraints = result.steps[0]?.output?.constraints as
      | { max_budget?: number; approval_threshold?: number }
      | undefined;
    const maxBudget = constraints?.max_budget;
    if (!maxBudget) return;
    const usedThreshold =
      typeof constraints?.approval_threshold === "number"
        ? constraints.approval_threshold
        : thresholdNum;
    setBusy(true);
    setError(null);
    try {
      const r = await createMission({
        instruction,
        budget: maxBudget,
        currency: "USD",
        approval_threshold: usedThreshold,
      });
      setCreated(r.transaction);
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  const canSubmit =
    result && result.final_decision !== "BLOCK" && result.final_decision !== "NEEDS_INFO";

  return (
    <div className="space-y-6">
      <Card className="p-6">
        <div className="mb-1 flex items-center gap-3">
          <h3 className="font-semibold text-white">Mission Orchestrator</h3>
          <Badge label="Dry run" className="bg-sky-500/10 text-sky-400 border-sky-500/30" />
        </div>
        <p className="mb-4 text-xs text-slate-500">
          Five specialist agents hand the mission along: Planner → Merchant → Risk → Policy →
          Supervisor. The agents are deterministic and rule-based (not independent LLMs). This
          never creates, approves, or executes a payment.
        </p>

        <label className="mb-1 block text-xs text-slate-400">Purchase mission</label>
        <textarea
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          rows={2}
          className="mb-3 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-emerald-500"
        />
        <div className="mb-4 grid grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-xs text-slate-400">
              Manual budget override (USD, optional)
            </label>
            <input
              value={budget}
              onChange={(e) => setBudget(e.target.value.replace(/[^0-9.]/g, ""))}
              placeholder="leave empty to use the mission text"
              inputMode="decimal"
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-emerald-500"
            />
            <p className="mt-1 text-xs text-slate-500">
              {detected !== null
                ? `Detected from mission: $${detected}`
                : "No budget detected in the mission text."}
              {manual !== undefined && detected !== null && manual !== detected && (
                <span className="text-amber-400">
                  {" "}
                  Override ${manual} differs; the lower value applies.
                </span>
              )}
              {manual === undefined && detected === null && (
                <span className="text-amber-400"> Enter a budget or say “under $N”.</span>
              )}
            </p>
          </div>
          <div>
            <label className="mb-1 block text-xs text-slate-400">Approval threshold (USD)</label>
            <input
              value={threshold}
              onChange={(e) => setThreshold(e.target.value.replace(/[^0-9.]/g, ""))}
              placeholder="200"
              inputMode="decimal"
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-emerald-500"
            />
            <p className="mt-1 text-xs text-slate-500">
              Amounts at or above this need human approval. Empty uses $200.
            </p>
          </div>
        </div>
        <button
          onClick={run}
          disabled={busy || instruction.trim().length < 3}
          className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-emerald-400 disabled:opacity-50"
        >
          {busy ? "Running agents…" : "Run agents"}
        </button>
        {error && <p className="mt-3 text-sm text-rose-400">{error}</p>}
      </Card>

      {result && (
        <>
          <Card className="p-6">
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <h3 className="font-semibold text-white">Supervisor summary</h3>
              <Badge
                label={`Policy: ${result.final_decision}`}
                className={DECISION_BADGE[result.final_decision] ?? DECISION_BADGE.NEEDS_INFO}
              />
              <Badge
                label="Payment execution: NOT EXECUTED"
                className="bg-emerald-500/10 text-emerald-400 border-emerald-500/30"
              />
              <Badge
                label={
                  result.human_authorization === "REQUIRED"
                    ? "Human authorization: REQUIRED"
                    : "Human authorization: N/A"
                }
                className="bg-slate-500/10 text-slate-300 border-slate-500/30"
              />
            </div>
            <p className="text-sm text-slate-300">{result.explanation}</p>
            <p className="mt-2 text-xs text-slate-500">
              The policy result is a recommendation, not payment authorization. These badges
              describe this dry run only; once a transaction is created, its live status is shown
              in the Approval Center and Transactions.
            </p>
            <p className="mt-2 text-xs text-slate-500">{result.mode_label}</p>

            {canSubmit && !created && (
              <button
                onClick={submitForApproval}
                disabled={busy}
                className="mt-4 rounded-lg border border-emerald-500/50 px-4 py-2 text-sm font-semibold text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-50"
              >
                Send to Approval Center
              </button>
            )}
            {canSubmit && !created && (
              <p className="mt-2 text-xs text-slate-500">
                This creates a pending transaction that waits in the Approval Center. No PayPal
                order exists until a person approves it and clicks execute.
              </p>
            )}
            {created && (
              <div className="mt-4 rounded-lg border border-slate-700 bg-slate-950/60 p-3 text-sm text-slate-300">
                Created transaction #{created.id} with status{" "}
                <span className="font-semibold text-white">{statusLabel(created.status)}</span>{" "}
                (policy recommendation {created.decision.replace(/_/g, " ")}).
                No PayPal order exists yet; a human must approve and execute it.{" "}
                <button onClick={goApproval} className="font-semibold text-emerald-400 underline">
                  Open Approval Center
                </button>
              </div>
            )}
          </Card>

          <Card className="p-6">
            <h3 className="mb-4 font-semibold text-white">Agent activity timeline</h3>
            <ol className="space-y-4">
              {result.steps.map((s, i) => (
                <StepCard key={s.agent} step={s} index={i + 1} />
              ))}
            </ol>
          </Card>
        </>
      )}
    </div>
  );
}

// Mirrors the backend Planner's extraction so the user can see what will be
// used before running. The backend remains the source of truth.
function detectBudget(text: string): number | null {
  const m = /(?:under|below|less than|max(?:imum)?|up to|within)\s*\$?\s*(\d+(?:\.\d+)?)/i.exec(text);
  return m ? Number(m[1]) : null;
}

function StepCard({ step, index }: { step: AgentStep; index: number }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-800 text-sm">
          {AGENT_ICON[step.agent] ?? index}
        </span>
        <span className="font-semibold text-white">
          {index}. {step.agent} agent
        </span>
        <Badge label={step.status} className={STEP_BADGE[step.status] ?? STEP_BADGE.skipped} />
        <span className="ml-auto text-xs text-slate-500">{step.duration_ms} ms</span>
      </div>
      <p className="mt-1 text-xs text-slate-500">{step.role}</p>
      <p className="mt-2 text-sm text-slate-300">{step.summary}</p>
      <p className="mt-2 text-xs text-emerald-400">↳ Handoff: {step.handoff}</p>
      <button
        onClick={() => setOpen(!open)}
        className="mt-2 text-xs text-slate-400 underline hover:text-slate-200"
      >
        {open ? "Hide" : "Show"} input / output
      </button>
      {open && (
        <div className="mt-2 grid gap-3 md:grid-cols-2">
          <pre className="overflow-auto rounded-lg bg-slate-950 p-3 text-[11px] text-slate-400">
            {JSON.stringify(step.input, null, 2)}
          </pre>
          <pre className="overflow-auto rounded-lg bg-slate-950 p-3 text-[11px] text-slate-400">
            {JSON.stringify(step.output, null, 2)}
          </pre>
        </div>
      )}
    </li>
  );
}
