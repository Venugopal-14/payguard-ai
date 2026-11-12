// Treat an empty string the same as unset. Docker/Next can inline an empty
// NEXT_PUBLIC_API_URL at build time, and `??` would NOT fall back for "",
// leaving API_URL="" so fetches hit the frontend origin and 404. Trimming to a
// falsy check guarantees we fall back to the backend URL.
export const API_URL =
  (process.env.NEXT_PUBLIC_API_URL || "").trim() || "http://localhost:8100";

export type Transaction = {
  id: number;
  mission_id: number | null;
  description: string;
  merchant: string;
  amount: number;
  currency: string;
  decision: string; // policy RECOMMENDATION: APPROVE | NEEDS_APPROVAL | BLOCK
  risk_score: number;
  risk_level: string;
  reasoning: string;
  status: string; // lifecycle: AWAITING_APPROVAL (shown "Pending approval") | APPROVED | ...
  paypal_order_id: string | null;
  paypal_capture_id: string | null;
  approve_url: string | null;
  created_at: string;
  human_approval?: "PENDING" | "APPROVED" | "NOT_APPLICABLE"; // derived, read-only
};

export type AgentView = {
  source: string;
  provider: string;
  intent: string;
  category: string;
  confidence: number;
  summary: string;
};

export type RiskFactor = {
  factor: string;
  weight: number;
  description: string;
};

export type RiskView = {
  score: number;
  level: string;
  signals: string[];
  factors: RiskFactor[];
  explanation: string;
  recommended_action: string;
};

export type ProposeResponse = {
  transaction: Transaction;
  agent_source: string;
  agent: AgentView;
  risk: RiskView;
  policy_reasons: string[];
};

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    // FastAPI errors look like {"detail": "..."}; show just the message.
    let msg = text;
    try {
      const d = JSON.parse(text)?.detail;
      if (typeof d === "string") msg = d;
    } catch {
      /* not JSON */
    }
    throw new Error(msg || `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

/** Outcome of a verified PayPal capture / checkout return (see backend). */
export type CheckoutResult = {
  outcome:
    | "COMPLETED"
    | "ALREADY_COMPLETED"
    | "PENDING"
    | "NOT_APPROVED"
    | "DECLINED_RETRY"
    | "RETRYABLE_ERROR"
    | "FAILED"
    | "CANCELLED"
    | "NOT_CAPTURABLE"
    | "NO_CHANGE";
  message: string;
  transaction: Transaction;
  retry_url: string | null;
};

/** True only for an https PayPal *Sandbox* checkout URL (never live PayPal). */
export function isSandboxCheckoutUrl(url: string | null | undefined): url is string {
  if (!url) return false;
  try {
    const u = new URL(url);
    const host = u.hostname.toLowerCase();
    return (
      u.protocol === "https:" &&
      (host === "sandbox.paypal.com" || host.endsWith(".sandbox.paypal.com"))
    );
  } catch {
    return false;
  }
}

/** Send the browser (same tab) to the PayPal-hosted Sandbox checkout. */
export function redirectToPayPal(url: string | null | undefined): void {
  if (!isSandboxCheckoutUrl(url)) {
    throw new Error("PayPal did not return a valid Sandbox checkout link.");
  }
  window.location.assign(url);
}

export async function paypalReturn(token: string, payerId: string | null): Promise<CheckoutResult> {
  const res = await fetch(`${API_URL}/api/paypal/return`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, payer_id: payerId || null }),
  });
  return j<CheckoutResult>(res);
}

export async function paypalCancel(token: string): Promise<CheckoutResult> {
  const res = await fetch(`${API_URL}/api/paypal/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  return j<CheckoutResult>(res);
}

export async function createMission(body: {
  instruction: string;
  budget: number;
  currency: string;
  approval_threshold: number;
}): Promise<ProposeResponse> {
  const res = await fetch(`${API_URL}/api/missions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return j<ProposeResponse>(res);
}

export async function approve(id: number): Promise<Transaction> {
  const res = await fetch(`${API_URL}/api/transactions/${id}/approve`, {
    method: "POST",
  });
  return j<Transaction>(res);
}

export async function execute(id: number): Promise<Transaction> {
  const res = await fetch(`${API_URL}/api/transactions/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transaction_id: id }),
  });
  return j<Transaction>(res);
}

export async function capture(id: number): Promise<Transaction> {
  const res = await fetch(`${API_URL}/api/transactions/capture`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transaction_id: id }),
  });
  return j<Transaction>(res);
}

export async function listTransactions(): Promise<Transaction[]> {
  const res = await fetch(`${API_URL}/api/transactions`, {
    cache: "no-store",
  });
  return j<Transaction[]>(res);
}

export async function health(): Promise<{
  status: string;
  paypal_configured: boolean;
  ai_configured: boolean;
  ai_provider: string;
}> {
  const res = await fetch(`${API_URL}/health`, { cache: "no-store" });
  return j(res);
}

export type FraudAnalytics = {
  label: string;
  total_transactions: number;
  pending_approval: number;
  decided_transactions: number;
  approved_transactions: number;
  approval_rate_pct: number | null;
  simulated_fraud_attempts_blocked: number;
  simulated_blocked_amount_by_currency: Record<string, number>;
  policy_blocked_amount_by_currency: Record<string, number>;
  risk_distribution: Record<string, number>;
  decision_breakdown: Record<string, number>;
};

export async function fraudAnalytics(): Promise<FraudAnalytics> {
  const res = await fetch(`${API_URL}/api/analytics/fraud`, {
    cache: "no-store",
  });
  return j<FraudAnalytics>(res);
}

export type AgentStep = {
  agent: string;
  role: string;
  status: string; // ok | flagged | blocked | incomplete | skipped
  summary: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  handoff: string;
  duration_ms: number;
};

export type OrchestrationResult = {
  mode: string;
  mode_label: string;
  final_decision: string; // policy RECOMMENDATION: APPROVE | NEEDS_APPROVAL | BLOCK | NEEDS_INFO
  execution_status: string; // NOT_EXECUTED
  human_authorization: string; // REQUIRED | NOT_APPLICABLE
  status_line: string;
  requires_human_approval: boolean;
  payment_executed: boolean;
  explanation: string;
  execution_proposal: Record<string, unknown> | null;
  steps: AgentStep[];
};

export async function orchestrate(body: {
  instruction: string;
  budget?: number;
  currency: string;
  approval_threshold: number;
}): Promise<OrchestrationResult> {
  const res = await fetch(`${API_URL}/api/orchestrate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return j<OrchestrationResult>(res);
}
