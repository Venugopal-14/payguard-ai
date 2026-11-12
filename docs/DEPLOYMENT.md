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

## 1. Create the PostgreSQL database (outside the Blueprint)

The database is **not** declared in `render.yaml`; it is created and managed
separately so the Blueprint never creates, recreates or overwrites it.

1. Render Dashboard -> **New** -> **PostgreSQL**.
2. Name it `payguard-db-new` (current instance), plan **Free**, same region as
   the services. Wait until its status is **Available**.
3. On the database page, copy the **Internal Database URL** (used by the
   backend over Render's private network). Treat it as a secret: never commit
   it, paste it into chat, or log it.

> **Free-tier expiry:** Render free PostgreSQL instances expire after a limited
> period. When that happens, create a new instance as above and point the
> backend's `DATABASE_URL` at it (step 3). The schema is created automatically
> on startup, so a fresh database needs no migration step.

## 2. Deploy the services with the Blueprint

1. Render Dashboard -> **New** -> **Blueprint**.
2. Connect the GitHub repo `payguard-ai`.
3. Render reads `render.yaml` and proposes: `payguard-backend`,
   `payguard-frontend` (no database). Click **Apply**.
4. Review every Blueprint sync before applying it. The proposed changes should
   never include deleting or creating a database. Consider setting the
   Blueprint's **Auto Sync** to **No** and using **Manual Sync**.

## 3. Set backend environment variables

On the **payguard-backend** service -> **Environment**, set the `sync:false`
values. Render only prompts for these on initial Blueprint creation and
ignores them on later syncs, so values set in the dashboard are preserved:

| Key | Value |
|-----|-------|
| `DATABASE_URL` | secret — the **Internal Database URL** of `payguard-db-new` |
| `PAYPAL_CLIENT_ID` | your sandbox client id |
| `PAYPAL_CLIENT_SECRET` | your sandbox secret |
| `PAYPAL_WEBHOOK_ID` | leave blank for now (set in step 7) |
| `FRONTEND_URL` | the frontend URL from step 2 (e.g. `https://payguard-frontend.onrender.com`) |
| `OPENAI_API_KEY` | optional — set for real AI, else blank uses the rule-based fallback |

> **AI note:** local Ollama (`host.docker.internal`) does NOT exist on Render.
> Either set `OPENAI_API_KEY` (production AI) or leave it blank — the app
> automatically uses the deterministic fallback agent and never crashes.

## 4. Set frontend environment variable

On **payguard-frontend** -> **Environment**:

| Key | Value |
|-----|-------|
| `NEXT_PUBLIC_API_URL` | the backend URL (e.g. `https://payguard-backend.onrender.com`) |

Trigger a redeploy of the frontend after setting this (it is baked at build).

## 5. Verify the public backend

Once both services are "Live":

```
GET https://<backend>/health      -> {"status":"ok", ... "webhook_configured":false}
GET https://<backend>/metrics     -> Prometheus text, HTTP 200
GET https://<backend>/docs        -> Swagger UI
GET https://<frontend>/           -> dashboard loads
```

## 6. Smoke-test the UI

Open the frontend URL and confirm: dashboard, Missions, Approval Center,
Transactions, transaction drawer, Audit Trail all load against the public API.

## 7. Create the PayPal webhook (only now)

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

## 8. Webhook verification test (no payment yet)

Use PayPal's **Webhook Simulator** (Developer Dashboard -> Webhooks Simulator):

1. Target URL: `https://<backend>/api/webhooks/paypal`
2. Send `PAYMENT.CAPTURE.COMPLETED`.
3. Expect HTTP 200. Check backend logs for `webhook_verified` /
   `webhook_processed`.
4. Send the **same** event again -> processed once (idempotency); logs show
   `webhook_duplicate`.

## 9. One controlled end-to-end sandbox payment

Only after the webhook test passes:

Prerequisites: backend `FRONTEND_URL` = the frontend's public URL (e.g.
`https://payguard-frontend-ybl6.onrender.com`) — PayPal's return/cancel
redirects are built from it — and `PAYPAL_BASE_URL` =
`https://api-m.sandbox.paypal.com` (the backend refuses anything else).

1. In the public UI, create a small mission (e.g. "Buy a USB cable", budget 5).
   It is created as **Pending approval**.
2. Approve it as a human (Missions page or Approval Center).
3. Click **Pay with PayPal (Sandbox)** (Missions page, or the transaction
   drawer from **Transactions**). The tab redirects to PayPal's hosted Sandbox
   checkout.
4. Sign in as the **Personal (buyer)** Sandbox account and approve.
5. PayPal redirects to `/paypal/return`; PayGuard captures and verifies the
   real PayPal response and shows the result (Completed with capture id, or
   Pending / Declined / Failed with a retry option).
6. Open the transaction drawer -> Audit tab: `HUMAN_APPROVED`,
   `ORDER_CREATED`, `PAYPAL_BUYER_RETURNED`, `CAPTURE_VERIFIED`. The
   `PAYMENT.CAPTURE.COMPLETED` webhook arrives later and is recorded; it never
   double-completes.

To test cancellation, use the cancel link on the PayPal checkout page: the
transaction stays `PAYPAL_CREATED` and `/paypal/cancel` offers **Retry
checkout**.

Do **not** create multiple test payments.

## 10. Notes & limitations

- **Free tier cold starts:** Render free services sleep after inactivity;
  the first request may take ~30–60s. Fine for judging; mention it in the demo.
- **AI in production:** set `OPENAI_API_KEY` for a live LLM, or accept the
  deterministic fallback (still fully functional and demoable).
- **Database:** schema is created on startup (`create_all` for `missions`,
  `transactions`, `audit_events`, `webhook_events`); `correlation_id` is added
  idempotently. No destructive reset is performed. The database is managed
  outside the Blueprint, so removing or changing `render.yaml` never deletes
  it. The previous `payguard-db` instance (expired free tier) was replaced by
  `payguard-db-new`, started fresh with no data carried over.
