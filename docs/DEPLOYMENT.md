# PayGuard AI — Render Deployment Guide (Phase 4D)

Public architecture:

```
Internet
  -> Render Frontend (Next.js)
  -> Render Backend  (FastAPI)   -> Render PostgreSQL
                                 -> PayPal Sandbox
PayPal Sandbox  --webhook-->  https://<backend>/api/webhooks/paypal
```

> **Secrets:** every value marked "secret" is entered in the Render dashboard
> (Environment tab), never committed. `.env` stays local and git-ignored.

---

## 0. Prerequisites

- The repo is pushed to GitHub (done): `Venugopal-14/payguard-ai`
- A Render account (free): <https://render.com>
- Your PayPal **Sandbox** Client ID + Secret (same ones used locally)

## 1. Deploy with the Blueprint

1. Render Dashboard -> **New** -> **Blueprint**.
2. Connect the GitHub repo `payguard-ai`.
3. Render reads `render.yaml` and proposes: `payguard-db`, `payguard-backend`,
   `payguard-frontend`. Click **Apply**.
4. Render provisions the PostgreSQL database and both services.

## 2. Set backend environment variables

On the **payguard-backend** service -> **Environment**, set the `sync:false`
values (DATABASE_URL is wired automatically from the database):

| Key | Value |
|-----|-------|
| `PAYPAL_CLIENT_ID` | your sandbox client id |
| `PAYPAL_CLIENT_SECRET` | your sandbox secret |
| `PAYPAL_WEBHOOK_ID` | leave blank for now (set in step 6) |
| `FRONTEND_URL` | the frontend URL from step 4 (e.g. `https://payguard-frontend.onrender.com`) |
| `OPENAI_API_KEY` | optional — set for real AI, else blank uses the rule-based fallback |

> **AI note:** local Ollama (`host.docker.internal`) does NOT exist on Render.
> Either set `OPENAI_API_KEY` (production AI) or leave it blank — the app
> automatically uses the deterministic fallback agent and never crashes.

## 3. Set frontend environment variable

On **payguard-frontend** -> **Environment**:

| Key | Value |
|-----|-------|
| `NEXT_PUBLIC_API_URL` | the backend URL (e.g. `https://payguard-backend.onrender.com`) |

Trigger a redeploy of the frontend after setting this (it is baked at build).

## 4. Verify the public backend

Once both services are "Live":

```
GET https://<backend>/health      -> {"status":"ok", ... "webhook_configured":false}
GET https://<backend>/metrics     -> Prometheus text, HTTP 200
GET https://<backend>/docs        -> Swagger UI
GET https://<frontend>/           -> dashboard loads
```

## 5. Smoke-test the UI

Open the frontend URL and confirm: dashboard, Missions, Approval Center,
Transactions, transaction drawer, Audit Trail all load against the public API.

## 6. Create the PayPal webhook (only now)

1. PayPal Developer Dashboard -> your app -> **Add Webhook**.
2. Webhook URL:
   ```
   https://<backend>/api/webhooks/paypal
   ```
3. Subscribe to these events:
   - `CHECKOUT.ORDER.APPROVED`
   - `PAYMENT.CAPTURE.PENDING`
   - `PAYMENT.CAPTURE.COMPLETED`
   - `PAYMENT.CAPTURE.DENIED`
   - `CHECKOUT.PAYMENT-APPROVAL.REVERSED`
4. Save -> copy the generated **Webhook ID**.
5. Put it in Render: backend -> Environment -> `PAYPAL_WEBHOOK_ID` -> save
   (this redeploys the backend).
6. Verify: `GET https://<backend>/health` now shows `"webhook_configured":true`.

## 7. Webhook verification test (no payment yet)

Use PayPal's **Webhook Simulator** (Developer Dashboard -> Webhooks Simulator):

1. Target URL: `https://<backend>/api/webhooks/paypal`
2. Send `PAYMENT.CAPTURE.COMPLETED`.
3. Expect HTTP 200. Check backend logs for `webhook_verified` /
   `webhook_processed`.
4. Send the **same** event again -> processed once (idempotency); logs show
   `webhook_duplicate`.

## 8. One controlled end-to-end sandbox payment

Only after the webhook test passes:

1. In the public UI, create a small mission (e.g. "Buy a USB cable", budget 50).
2. Agent proposes -> risk -> policy -> (approve if needed) -> **Create PayPal
   order**.
3. Approve in PayPal as the **Personal (buyer)** sandbox account.
4. Capture -> transaction reaches `COMPLETED`; the webhook also reconciles it.
5. Open the transaction drawer -> Audit tab shows the full lifecycle.

Do **not** create multiple test payments.

## 9. Notes & limitations

- **Free tier cold starts:** Render free services sleep after inactivity;
  the first request may take ~30–60s. Fine for judging; mention it in the demo.
- **AI in production:** set `OPENAI_API_KEY` for a live LLM, or accept the
  deterministic fallback (still fully functional and demoable).
- **Database:** schema is created on startup; `correlation_id` is added
  idempotently. No destructive reset is performed.
