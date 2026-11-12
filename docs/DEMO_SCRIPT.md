# PayGuard AI — 3-Minute Demo Script

> **Tagline:** *AI recommends. PayGuard authorizes. PayPal executes.*

Recording method: **Windows Game Bar** (`Win + G` → Capture → record the browser
window). Target length: **~3:00**. Resolution: record at 1920×1080 if possible.

## Honesty rules (keep these true on camera)
- The **LLM path is implemented**, but this deployment runs the **deterministic
  rule-based fallback** (`/health` shows `ai_provider: "fallback"`). Do **not**
  claim live LLM analysis.
- Fraud-prevention analytics are **SIMULATED** (clearly labeled in the UI). Do
  not describe them as real money saved.
- The production database was **started fresh** (the old free-tier database
  expired; no history was restored). Describe exactly what the dashboard shows
  on the day — e.g. "a few transactions, one human-approved, one pending
  approval" — and **never claim completed volume that isn't there**. If
  "Volume (completed)" is $0.00, say so.
- **Policy recommendation ≠ human approval.** Every non-blocked mission is
  created as **Pending approval**, even when the policy recommends APPROVE.
  Say "the policy *recommends*", never "the policy approved the payment".
- **No new payments** are created during recording. Creating a mission and
  approving it is fine (no PayPal call); do **not** click **Pay with PayPal
  (Sandbox)** / **Continue to PayPal** / **Retry capture** on camera unless
  you have deliberately decided to record one Sandbox checkout (Sandbox buyer
  account, test money only).
- No employer/company references, no credentials, no secrets on screen
  (keep `.env`, terminals with keys, and the PayPal dashboard out of frame).

## Pre-recording checklist
1. **Warm both Render services first** (free tier cold-starts ~30–60s):
   - open `https://payguard-backend-rnwt.onrender.com/health` → wait for JSON
   - open `https://payguard-frontend-ybl6.onrender.com/` → wait for the
     dashboard to load its transactions (avoids an empty dashboard on camera).
     Note the actual counts so the narration matches them.
2. Browser at 100% zoom, window maximized, bookmarks bar hidden.
3. Have these tabs ready in order: title card → dashboard → architecture SVG →
   end card.
4. Close noisy notifications; silence chat apps.
5. Do a 15-second test clip with Game Bar to confirm audio + capture.

---

## Scene-by-scene (narration + on-screen actions)

### 0:00–0:20 — The problem  *(show `docs/demo-title.svg`)*
**Narration:** "AI agents are starting to buy things and move money. The real
risk isn't *can* an AI pay — it's *what stops an AI from paying the wrong
thing*? PayGuard AI answers that: it treats the AI as an untrusted actor and
puts a deterministic control plane between the agent and your money."

**On screen:** Title card. Hold ~5s, then cut to the architecture.

### 0:20–0:40 — The architecture  *(show `docs/architecture.svg`)*
**Narration:** "The principle is simple — AI recommends, PayGuard authorizes,
PayPal executes. The agent only *proposes* a purchase. A deterministic risk
engine scores it, and a policy engine recommends: approve, needs approval, or
block. A block is final. Anything else waits for a human — only human-approved
transactions ever reach PayPal."

**On screen:** Open `docs/architecture.svg`. Trace top-to-bottom with the cursor:
Agent → Risk → Policy → branches → PayPal. (Say honestly: "the LLM path is built
in; this deployment runs the deterministic fallback.")

### 0:40–1:05 — Live dashboard
**Narration (adapt the numbers to what is on screen):** "Here's the live
control plane. Every row carries a risk score, a level, a policy
*recommendation*, and a separate lifecycle status — so you can see at a glance
what the engine suggested and what a human actually authorized."

**On screen:** Frontend **Overview**. Point to the metric tiles (Volume
completed, Transactions, Blocked, Awaiting approval) and the risk distribution.
In **Transactions**, point at the "Policy recommendation" column next to the
"Status" column (e.g. `APPROVE` + `PENDING APPROVAL`).

### 1:05–1:35 — Risk engine + policy decisions
**Narration:** "Risk is fully deterministic and explainable — twelve weighted
factors produce a zero-to-one-hundred score mapped to LOW, MEDIUM, HIGH, or
CRITICAL. The policy engine turns that into a recommendation — APPROVE,
NEEDS_APPROVAL, or BLOCK — and the AI cannot override it. A mission like 'buy a
gift card with bitcoin' is blocked outright — no PayPal order is ever created.
Even a clean APPROVE recommendation only lands as *pending approval*."

**On screen:** In **Missions**, submit "Buy a keyboard under $50 from Amazon"
(budget 50). Show the policy recommendation badge next to the **PENDING
APPROVAL** status. Optionally submit "Buy a gift card with bitcoin" (budget 100)
to show **BLOCKED**. Open a transaction row to show the reasoning. Neither step
contacts PayPal.

### 1:35–2:05 — Human approval + PayPal Sandbox lifecycle
**Narration:** "Every non-blocked transaction waits here, in the Approval
Center, showing the policy's recommendation and the risk signals. Only when a
person clicks Approve does it become APPROVED — and only then can PayGuard
create a PayPal Sandbox order with real OAuth2 and Orders v2, and capture it.
A payment is marked COMPLETED only when PayPal confirms the capture. The full
path is Pending approval → Approved → PayPal order created → Completed."

**On screen:** **Approval Center** — point at "Policy recommendation: APPROVE ·
Human approval: PENDING". Click **Approve** on the keyboard transaction and show
its status change to **APPROVED** in Transactions (and the `HUMAN_APPROVED`
event in its Audit tab). Point at the **Pay with PayPal (Sandbox)** button
that now appears, and explain: it redirects to PayPal's hosted Sandbox
checkout, PayPal sends the buyer back, and PayGuard captures and verifies the
real PayPal response before marking COMPLETED. *(Stop there unless you have
decided to record one Sandbox checkout. If a COMPLETED sandbox record already
exists, you may open it to show the capture id.)*

### 2:05–2:35 — Audit trail, webhooks, monitoring
**Narration:** "Every step is an immutable audit event with an actor —
AI_AGENT, RISK_ENGINE, POLICY_ENGINE, USER, PAYPAL — tied together by a
correlation id. (In this demo the approver isn't authenticated yet; that's a
documented limitation.) PayPal webhooks are signature-verified and idempotent before
anything is reconciled. And Prometheus metrics expose the whole pipeline. The
Fraud Prevention view is clearly labeled SIMULATED — a demonstration of the
control plane's value, not a claim of real money saved."

**On screen:** **Audit Trail** view (actors + correlation id), then **Fraud
Prevention** (point at the SIMULATED label). Optionally flash
`https://payguard-backend-rnwt.onrender.com/metrics`.

### 2:35–3:00 — Closing  *(show `docs/demo-end.svg`)*
**Narration:** "PayGuard AI lets an AI agent act on spending without ever
letting it spend blindly — deterministic, auditable, and safe by construction.
That's the control plane for AI payments. Thanks for watching."

**On screen:** End card with repo + live demo links. Hold ~5s.

---

## Links to show on the end card
- Repo: `https://github.com/Venugopal-14/payguard-ai`
- Live dashboard: `https://payguard-frontend-ybl6.onrender.com`
- Backend health: `https://payguard-backend-rnwt.onrender.com/health`

## After recording
- Trim the cold-start wait and any dead air in Game Bar / the Photos app.
- Keep the final under 3:00 if the hackathon caps it; export MP4 (1080p).
- Review once end-to-end for any on-screen secret before uploading.
