# PayGuard AI

> **Let AI act. Never let AI spend blindly.**

PayGuard AI is a secure payment control plane for AI agents. An AI agent can
understand a natural-language financial mission and *propose* a purchase — but
it can **never move money on its own**. Every proposal passes through a
deterministic **risk engine** and **policy engine** that decide whether it is
auto-approved, requires human approval, or is blocked. Only approved
transactions ever reach **PayPal**.

Built for the **Build What's Next with PayPal and AI** hackathon.

---

## Why this is different

Most "AI + payments" projects are a chatbot with a checkout button. PayGuard
inverts the trust model: the AI is treated as an untrusted actor, and a
deterministic, auditable control plane governs what it is allowed to do with
money.

```
            USER mission + budget + policy
                        │
                        ▼
                ┌───────────────┐
                │   AI AGENT    │  proposes a purchase (never pays)
                └───────┬───────┘
                        ▼
                ┌───────────────┐
                │  RISK ENGINE  │  deterministic 0-100 score + signals
                └───────┬───────┘
                        ▼
                ┌───────────────┐
                │ POLICY ENGINE │  deterministic decision
                └───────┬───────┘
          ┌─────────────┼──────────────┐
          ▼             ▼              ▼
        BLOCK     NEEDS_APPROVAL     APPROVE
                        │              │
                   human approves      │
                        └──────┬───────┘
                               ▼
                        PAYPAL SANDBOX
                     order → approve → capture
                               ▼
                      PostgreSQL audit trail
```

---

## Features

- **Natural-language missions** — "Buy a mechanical keyboard under $120 from a
  trusted store."
- **AI agent proposal** — uses any OpenAI-compatible LLM, with a deterministic
  rule-based fallback so the demo always works (even with no API key).
- **Risk engine** — scores unknown merchants, suspicious terms, budget burn
  rate, and absolute amount into an explainable 0-100 risk score.
- **Policy engine** — budget limits, approval thresholds, and risk-based rules
  produce an auditable `APPROVE` / `NEEDS_APPROVAL` / `BLOCK` decision.
- **Human-in-the-loop** — anything above the threshold or at medium risk waits
  for an explicit human approval.
- **PayPal Sandbox** — real Orders v2 flow: create order → buyer approval →
  capture.
- **Audit trail** — every proposal, decision, and reason is persisted in
  PostgreSQL and shown in the UI.

---

## Tech stack

| Layer     | Tech                                        |
| --------- | ------------------------------------------- |
| Frontend  | Next.js 14 (App Router), TypeScript, Tailwind |
| Backend   | FastAPI, SQLAlchemy 2, Pydantic             |
| Database  | PostgreSQL 17                               |
| Payments  | PayPal REST (OAuth2 + Orders v2), Sandbox   |
| AI        | Any OpenAI-compatible API (or local Ollama) |
| Infra     | Docker + Docker Compose                     |

---

## Run it

### Prerequisites

- Docker Desktop

That's it. Node and Python run inside containers.

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and add your **PayPal Sandbox** credentials from
<https://developer.paypal.com/dashboard/>:

```env
PAYPAL_CLIENT_ID=your_sandbox_client_id
PAYPAL_CLIENT_SECRET=your_sandbox_client_secret
```

Optionally add an AI key (otherwise the rule-based agent is used):

```env
OPENAI_API_KEY=sk-...
```

### 2. Start the stack

```bash
docker compose up --build
```

| Service   | URL                            |
| --------- | ------------------------------ |
| Frontend  | http://localhost:3100          |
| Backend   | http://localhost:8100          |
| API docs  | http://localhost:8100/docs     |
| Health    | http://localhost:8100/health   |

### 3. Try the flow

1. Open http://localhost:3100
2. Enter a mission + budget, click **Let the agent propose**.
3. See the agent proposal, risk score, and the policy decision.
4. If approval is required, click **Approve as human**.
5. Click **Create PayPal order**, open the approval link, and sign in with your
   **PayPal Sandbox personal (buyer) account**.
6. Back in PayGuard, click **Capture payment**.
7. The transaction appears in the audit trail as `COMPLETED`.

---

## PayPal Sandbox test accounts

In the PayPal Developer Dashboard → **Testing Tools → Sandbox Accounts** you
get a business (merchant) and a personal (buyer) account. Use the **personal**
account to approve payments.

---

## Project layout

```
payguard-ai/
├── backend/
│   └── app/
│       ├── agent/      # AI agent (LLM + fallback) — proposes only
│       ├── engine/     # risk + policy engines (deterministic)
│       ├── payments/   # PayPal Sandbox integration (only layer with creds)
│       ├── api/        # FastAPI routes (the control-plane flow)
│       ├── models/     # SQLAlchemy models
│       └── schemas/    # Pydantic schemas
├── frontend/           # Next.js UI
├── docker-compose.yml
├── .env.example
├── SECURITY.md
└── README.md
```

---

## Security model

See [SECURITY.md](./SECURITY.md). In short: the AI proposes, deterministic
engines dispose, and only the PayPal layer holds credentials. A blocked
decision can never create a PayPal order.

## License

[MIT](./LICENSE)
