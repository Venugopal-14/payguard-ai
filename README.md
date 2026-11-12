# PayGuard AI

> **Let AI act. Never let AI spend blindly.**

PayGuard AI is a secure **payment control plane for AI agents**. An AI agent
understands a natural-language financial mission and *proposes* a purchase — but
it can **never move money on its own**. Every proposal passes through a
deterministic **risk engine** and **policy engine** that recommend approve,
needs-approval, or block. Blocked proposals stop there; **every other
proposal waits for an explicit human approval** — the policy recommendation
never authorizes a payment by itself. Only human-approved transactions ever
reach **PayPal**, and every step is recorded in an auditable trail.

Built for the **Build What's Next with PayPal and AI** hackathon.

**Core principle:** `AI recommends → PayGuard authorizes → PayPal executes.`

---

## The problem

AI agents are starting to buy things and move money. The real risk isn't "can
an AI pay?" — it's "what stops an AI from paying the wrong thing?" Most
"AI + payments" demos are a chatbot wired to a checkout button, where the model
effectively controls the money. PayGuard inverts that trust model: **the AI is
treated as an untrusted actor**, and a deterministic, auditable control plane
governs exactly what it is allowed to do.

---

## Architecture

A full architecture diagram and component breakdown live in
[docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)
([SVG](./docs/architecture.svg) · [Mermaid source](./docs/architecture.mmd)).

```
                 USER mission + budget + policy
                              │
                              ▼
                     ┌─────────────────┐
                     │    AI AGENT     │  proposes a purchase (never pays)
                     │ Ollama / OpenAI │  structured output, deterministic
                     │   + fallback    │  rule-based fallback if no LLM
                     └────────┬────────┘
                              ▼
                     ┌─────────────────┐
                     │   RISK ENGINE   │  explainable 0–100 score, 12 factors,
                     │ (deterministic) │  LOW / MEDIUM / HIGH / CRITICAL
                     └────────┬────────┘
                              ▼
                     ┌─────────────────┐
                     │  POLICY ENGINE  │  the authority — AI cannot bypass it
                     │ (deterministic) │
                     └────────┬────────┘
             ┌────────────────┼─────────────────┐
             ▼                ▼                 ▼
           BLOCK       NEEDS_APPROVAL         APPROVE     (recommendation)
                              │                 │
                              └────────┬────────┘
                                       ▼
                          PENDING APPROVAL (AWAITING_APPROVAL)
                                       │
                               human approves → APPROVED
                                       ▼
                              ┌─────────────────┐
                              │  PAYPAL SANDBOX │  OAuth2 → Orders v2
                              │  order→approve  │  → capture
                              │   →capture      │
                              └────────┬────────┘
                                       ▼
                              ┌─────────────────┐
                              │ WEBHOOK (signed │  verify → idempotent
                              │  + verified)    │  reconcile → audit
                              └────────┬────────┘
                                       ▼
             ┌─────────────────────────┼───────────────────────────┐
             ▼                         ▼                           ▼
     State machine            Audit trail (per-txn            Prometheus
     (valid transitions)      correlation IDs, actors)        /metrics
```

---

## Key features

- **Natural-language missions** — e.g. *"Buy a mechanical keyboard under $120
  from a trusted store."*
- **Real AI agent** — any OpenAI-compatible LLM (local **Ollama** or OpenAI),
  returning *structured, validated* proposals; a deterministic rule-based
  fallback keeps the system working with no API key.
- **Explainable risk engine** — 12 deterministic factors (unknown/blocked
  merchant, suspicious terms, budget burn, velocity, behavioral baseline,
  currency anomaly, …) → a 0–100 score and LOW/MEDIUM/HIGH/CRITICAL level with
  human-readable reasons.
- **Policy engine** — budget limits, approval thresholds, and risk rules produce
  an auditable `APPROVE` / `NEEDS_APPROVAL` / `BLOCK` **recommendation**. **The
  AI cannot override it.** `BLOCK` is final; the other two never authorize a
  payment on their own.
- **Human-in-the-loop** — every non-blocked transaction starts as **Pending
  approval** (`AWAITING_APPROVAL`) and becomes `APPROVED` only after explicit
  human approval. The recommendation (`decision`) and the lifecycle `status`
  are stored separately, and the API also returns a derived `human_approval`
  field (`PENDING` / `APPROVED` / `NOT_APPLICABLE`). `COMPLETED` is set only
  after PayPal reports the capture as completed (capture API or verified
  webhook).
- **PayPal Sandbox checkout** — real OAuth2 + Orders v2: create → redirect to
  PayPal's hosted Sandbox checkout → buyer approval → return → capture,
  verified against PayPal's response (order id, capture status, amount,
  currency) before `COMPLETED`. Stable per-transaction `PayPal-Request-Id`s
  plus per-transaction locking make duplicate clicks/returns safe. Sandbox-only
  by code, not just configuration.
- **Verified webhooks** — signature-verified via PayPal's verification API,
  idempotent (deduped by event id), reconciling transactions through an audited
  state machine.
- **Event & audit architecture** — every lifecycle transition is an immutable
  audit event with an actor (`AI_AGENT`, `RISK_ENGINE`, `POLICY_ENGINE`, `USER`,
  `PAYPAL`, `PAYPAL_WEBHOOK`, `RECONCILER`) and a correlation ID tying the whole
  chain together.
- **Observability** — Prometheus `/metrics` for AI, risk, policy, approvals,
  PayPal, webhooks, reconciliation, audit, and HTTP latency.
- **Fraud-prevention dashboard** — **clearly labeled SIMULATED** analytics:
  simulated fraud attempts blocked, blocked amount (per-currency), approval
  rate, risk distribution, and decision breakdown — all computed from persisted
  data. Completed payments are excluded from "blocked" figures, and ordinary
  budget blocks are kept separate from scam-type blocks.

---

## Multi-Agent Mission Orchestrator

Open **Mission Orchestrator** in the dashboard, enter a purchase mission, and
watch five specialist agents hand it along as an activity timeline:

1. **Planner** extracts the budget and constraints from the mission.
2. **Merchant** proposes a purchase and classifies the merchant against
   PayGuard's internal known/blocked lists.
3. **Risk** scores it with the existing 12-factor risk engine.
4. **Policy** applies the existing policy engine (approve / needs approval / block).
5. **Supervisor** summarises the result and hands off to a human.

The result keeps three things separate: the policy recommendation, the payment
execution status (always **NOT EXECUTED** here), and whether human authorization
is required. **Send to Approval Center** creates a pending transaction that waits
for a person to approve it; no PayPal order exists until they approve and execute.

> The five agents are deterministic and rule-based, not independent LLM agents,
> and the deployed app runs the deterministic fallback proposal agent. The
> endpoint is read-only but unauthenticated. See
> [Known Limitations](./docs/ARCHITECTURE.md#known-limitations).

---

## Security model

See [SECURITY.md](./SECURITY.md). In short:

1. The **AI proposes**; it has no tool that can approve, capture, move money, or
   change risk/policy. Adversarial prompts ("ignore the limit, capture now")
   cannot bypass the deterministic engines.
2. The **risk and policy engines are the authority** and are fully
   deterministic + explainable.
3. Only the **PayPal layer holds credentials**. A `BLOCK` can never create a
   PayPal order.
4. Webhooks are **signature-verified** before processing; unverified payloads
   are rejected. Secrets are never logged.

This is backed by an automated test suite (**60 tests**) including adversarial
guardrail tests proving the AI cannot bypass policy, risk, or payment controls.

---

## Tech stack

| Layer      | Technology |
| ---------- | ---------- |
| Frontend   | Next.js 14 (App Router), TypeScript, Tailwind |
| Backend    | FastAPI, SQLAlchemy 2, Pydantic |
| Database   | PostgreSQL 17 |
| Payments   | PayPal REST — OAuth2 + Orders v2 + Webhooks (Sandbox) |
| AI         | Ollama (local) or any OpenAI-compatible API, with rule-based fallback |
| Observability | Prometheus client (`/metrics`) |
| Infra      | Docker + Docker Compose; deployable to Render |

---

## Run it locally

### Prerequisites
- Docker Desktop (Node and Python run inside containers)

### 1. Configure environment
```bash
cp .env.example .env
```
Add your **PayPal Sandbox** credentials from
<https://developer.paypal.com/dashboard/>:
```env
PAYPAL_CLIENT_ID=your_sandbox_client_id
PAYPAL_CLIENT_SECRET=your_sandbox_client_secret
```
AI is optional — leave `OPENAI_API_KEY` blank to use the deterministic fallback,
or point `OLLAMA_BASE_URL` / `OLLAMA_MODEL` at a local Ollama model.

### 2. Start the stack
```bash
docker compose up --build
```

| Service  | URL |
| -------- | --- |
| Frontend | http://localhost:3100 |
| Backend  | http://localhost:8100 |
| API docs | http://localhost:8100/docs |
| Health   | http://localhost:8100/health |
| Metrics  | http://localhost:8100/metrics |

### 3. Try the flow
1. Open http://localhost:3100 and give the agent a mission + budget.
2. See the agent proposal, risk gauge, and policy recommendation. The
   transaction is created as **Pending approval** (unless blocked).
3. Approve it as a human (Missions page or Approval Center).
4. Click **Pay with PayPal (Sandbox)** (Missions page, or open the transaction
   from **Transactions**). PayGuard creates a PayPal **Sandbox** order and
   redirects this tab to PayPal's hosted Sandbox checkout.
5. Sign in with your **Sandbox personal (buyer)** account and approve the test
   payment. PayPal redirects back to `/paypal/return`, where PayGuard captures
   the order and **verifies PayPal's actual response** (order id, capture
   status, amount and currency) before marking it `COMPLETED`.
6. The audit trail shows `HUMAN_APPROVED` → `ORDER_CREATED` →
   `PAYPAL_BUYER_RETURNED` → `CAPTURE_VERIFIED`, with the PayPal order and
   capture ids.

### PayPal checkout states

| Situation | Result |
| --- | --- |
| Capture confirmed `COMPLETED`, amount/currency match | `COMPLETED` |
| Buyer cancels at PayPal | stays `PAYPAL_CREATED`; **Retry checkout** link |
| Capture `PENDING` at PayPal | stays `PAYPAL_CREATED`; the verified `PAYMENT.CAPTURE.COMPLETED` webhook completes it |
| `ORDER_NOT_APPROVED` / `INSTRUMENT_DECLINED` | stays `PAYPAL_CREATED`; buyer can return to PayPal |
| Network error / PayPal 5xx | unchanged; safe to retry (stable `PayPal-Request-Id`) |
| Capture `DECLINED`, amount/order mismatch, other PayPal rejection | `FAILED` |
| Duplicate execute / return / capture | same order reused; never captured twice |

PayGuard is **Sandbox-only**: the backend refuses any `PAYPAL_BASE_URL` other
than `https://api-m.sandbox.paypal.com`, and both backend and frontend only
accept checkout links on `*.sandbox.paypal.com`. `FRONTEND_URL` must be the
frontend's public URL, because PayPal's return/cancel redirects use it.
7. Explore **Fraud Prevention** and **Risk Analytics** for the simulated
   security analytics.

> **Try the guardrail:** a mission like *"Buy a gift card from unknownvendor"*
> is blocked by the policy engine — no PayPal order is created.

---

## Deployment (Render)

A Docker-based blueprint (`render.yaml`) deploys the frontend, backend, and
PostgreSQL. See [docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md) for the full
walkthrough, including configuring the PayPal webhook against the public HTTPS
backend. All secrets are set in the Render dashboard, never committed.

---

## Project layout

```
payguard-ai/
├── backend/
│   └── app/
│       ├── agent/      # AI agent (LLM + fallback) — proposes only
│       ├── engine/     # risk, policy, lifecycle (state machine), analytics
│       ├── payments/   # PayPal Sandbox + webhook verification/reconciliation
│       ├── api/        # FastAPI routes (control plane + analytics + webhooks)
│       ├── core/       # config + Prometheus metrics
│       ├── models/     # SQLAlchemy models (transaction, audit, webhook event)
│       └── schemas/    # Pydantic schemas
│   └── tests/          # 60 tests incl. adversarial guardrail tests
├── frontend/           # Next.js control-plane UI
├── docs/DEPLOYMENT.md
├── render.yaml
├── docker-compose.yml
├── .env.example
├── SECURITY.md
└── README.md
```

---

## Testing

```bash
docker compose run --rm backend python -m pytest -q
```
Covers the risk engine, policy decisions, PayPal order/capture (mocked),
webhook signature verification + idempotency, the audited state machine,
observability metrics, fraud analytics, and **adversarial security tests** that
prove the AI cannot bypass the controls.

---

## A note on honesty

The Fraud Prevention metrics are explicitly **simulated** — derived from the
risk/policy engine's decisions on test missions in the PayPal **Sandbox**. They
are a demonstration of the control plane's value, **not** a claim of real-world
money saved. Completed sandbox payments are never counted as "savings."

## License

[MIT](./LICENSE)
