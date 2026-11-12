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
- The dashboard shows **6 transactions across different lifecycle states**
  (COMPLETED, APPROVED, PAYPAL_CREATED) with **$3.20 in *completed* volume** —
  not "6 completed payments."
- **No new payments** are created during recording. We show existing records.
- No employer/company references, no credentials, no secrets on screen
  (keep `.env`, terminals with keys, and the PayPal dashboard out of frame).

## Pre-recording checklist
1. **Warm both Render services first** (free tier cold-starts ~30–60s):
   - open `https://payguard-backend-rnwt.onrender.com/health` → wait for JSON
   - open `https://payguard-frontend-ybl6.onrender.com/` → wait for the
     dashboard to populate the 6 transactions (avoids an empty dashboard on
     camera).
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
engine scores it, and a policy engine decides: approve, require human approval,
or block. Only authorized transactions ever reach PayPal."

**On screen:** Open `docs/architecture.svg`. Trace top-to-bottom with the cursor:
Agent → Risk → Policy → branches → PayPal. (Say honestly: "the LLM path is built
in; this deployment runs the deterministic fallback.")

### 0:40–1:05 — Live dashboard
**Narration:** "Here's the live control plane. Six transactions across the
lifecycle — some COMPLETED, some APPROVED, one awaiting a PayPal capture — with
three dollars twenty in *completed* volume. Every row carries a risk score, a
level, and a policy decision."

**On screen:** Frontend **Overview**. Point to the metric tiles (Volume
completed, Transactions, Blocked, Awaiting approval) and the risk distribution.

### 1:05–1:35 — Risk engine + policy decisions
**Narration:** "Risk is fully deterministic and explainable — twelve weighted
factors produce a zero-to-one-hundred score mapped to LOW, MEDIUM, HIGH, or
CRITICAL. The policy engine turns that into APPROVE, NEEDS_APPROVAL, or BLOCK,
and the AI cannot override it. A mission like 'buy a gift card from an unknown
vendor' is blocked outright — no PayPal order is ever created."

**On screen:** Open a transaction row to show the reasoning (agent note, risk
signals, policy reasons). Open **Risk Analytics** to show the distribution.

### 1:35–2:05 — Human approval + PayPal Sandbox lifecycle
**Narration:** "Anything medium-risk or above the approval threshold waits for a
human in the Approval Center. Only after approval does PayGuard create a PayPal
Sandbox order — real OAuth2 and Orders v2 — then capture the funds. You can see
a transaction move APPROVED → PAYPAL_CREATED → COMPLETED, with its real PayPal
capture id."

**On screen:** **Approval Center**, then a COMPLETED transaction's detail
showing the capture id and state path. *(Do not run a new capture — show the
existing completed record.)*

### 2:05–2:35 — Audit trail, webhooks, monitoring
**Narration:** "Every step is an immutable audit event with an actor —
AI_AGENT, RISK_ENGINE, POLICY_ENGINE, USER, PAYPAL — tied together by a
correlation id. PayPal webhooks are signature-verified and idempotent before
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
