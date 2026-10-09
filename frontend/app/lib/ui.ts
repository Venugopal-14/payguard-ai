import type { Transaction } from "./api";

export const RISK_TEXT: Record<string, string> = {
  LOW: "text-emerald-400",
  MEDIUM: "text-amber-400",
  HIGH: "text-rose-400",
  CRITICAL: "text-rose-500",
};

export const RISK_BADGE: Record<string, string> = {
  LOW: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  MEDIUM: "bg-amber-500/10 text-amber-400 border-amber-500/30",
  HIGH: "bg-rose-500/10 text-rose-400 border-rose-500/30",
  CRITICAL: "bg-rose-500/20 text-rose-400 border-rose-500/50",
};

export const RISK_STROKE: Record<string, string> = {
  LOW: "#34d399",
  MEDIUM: "#fbbf24",
  HIGH: "#fb7185",
  CRITICAL: "#f43f5e",
};

export const STATUS_BADGE: Record<string, string> = {
  COMPLETED: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  APPROVED: "bg-sky-500/10 text-sky-400 border-sky-500/30",
  AWAITING_APPROVAL: "bg-amber-500/10 text-amber-400 border-amber-500/30",
  PAYPAL_CREATED: "bg-sky-500/10 text-sky-400 border-sky-500/30",
  BLOCKED: "bg-rose-500/10 text-rose-400 border-rose-500/30",
  FAILED: "bg-rose-500/10 text-rose-400 border-rose-500/30",
};

export const DECISION_BADGE: Record<string, string> = {
  APPROVE: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  NEEDS_APPROVAL: "bg-amber-500/10 text-amber-400 border-amber-500/30",
  BLOCK: "bg-rose-500/10 text-rose-400 border-rose-500/30",
};

export function fmtMoney(amount: number, currency = "USD"): string {
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
    }).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency}`;
  }
}

export function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Parse the stored reasoning string into Agent / Risk / Policy sections. */
export function parseReasoning(reasoning: string): {
  agent: string[];
  risk: string[];
  policy: string[];
} {
  const agent: string[] = [];
  const risk: string[] = [];
  const policy: string[] = [];
  for (const line of (reasoning || "").split("\n")) {
    const t = line.trim();
    if (!t) continue;
    if (t.startsWith("Agent")) agent.push(t.replace(/^Agent[^:]*:\s*/, ""));
    else if (t.startsWith("Risk:")) risk.push(t.replace(/^Risk:\s*/, ""));
    else if (t.startsWith("Policy:")) policy.push(t.replace(/^Policy:\s*/, ""));
  }
  return { agent, risk, policy };
}

export type Metrics = {
  total: number;
  approved: number;
  blocked: number;
  awaiting: number;
  completed: number;
  volume: number;
};

export function computeMetrics(history: Transaction[]): Metrics {
  const total = history.length;
  const approved = history.filter((t) =>
    ["APPROVED", "PAYPAL_CREATED", "COMPLETED"].includes(t.status)
  ).length;
  const blocked = history.filter((t) => t.status === "BLOCKED").length;
  const awaiting = history.filter((t) => t.status === "AWAITING_APPROVAL").length;
  const completed = history.filter((t) => t.status === "COMPLETED").length;
  const volume = history
    .filter((t) => t.status === "COMPLETED")
    .reduce((s, t) => s + Number(t.amount), 0);
  return { total, approved, blocked, awaiting, completed, volume };
}
