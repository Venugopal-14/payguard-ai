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
  decision: string;
  risk_score: number;
  risk_level: string;
  reasoning: string;
  status: string;
  paypal_order_id: string | null;
  paypal_capture_id: string | null;
  approve_url: string | null;
  created_at: string;
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
    throw new Error(text || `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
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
