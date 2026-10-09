"""PayPal webhook endpoint.

Flow: receive raw body -> verify signature with PayPal -> dedupe by event id
-> reconcile transaction -> record audit event. Never trusts an unverified
payload; never leaks secrets or internal exceptions to the caller.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core import metrics
from app.db.database import get_db
from app.payments import paypal
from app.payments.webhooks import process_event

logger = logging.getLogger("payguard.webhooks")

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/paypal")
async def paypal_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    headers = dict(request.headers)
    logger.info("webhook_received bytes=%s", len(raw))

    # 1. Verify authenticity BEFORE processing anything.
    try:
        verified = await paypal.verify_webhook_signature(headers=headers, raw_body=raw)
    except paypal.PayPalNotConfigured:
        return JSONResponse({"error": "webhook not configured"}, status_code=503)
    except Exception:
        logger.warning("webhook_verify_error")
        return JSONResponse({"error": "verification failed"}, status_code=400)

    if not verified:
        metrics.webhook_verification_failures_total.inc()
        logger.warning("webhook_rejected reason=invalid_signature")
        return JSONResponse({"error": "invalid signature"}, status_code=401)
    logger.info("webhook_verified")

    # 2. Parse (already known valid JSON since verification parsed it too).
    try:
        event = json.loads(raw.decode("utf-8"))
    except Exception:
        return JSONResponse({"error": "malformed payload"}, status_code=400)

    event_id = event.get("id")
    event_type = event.get("event_type")
    resource = event.get("resource") or {}
    if not event_id or not event_type:
        return JSONResponse({"error": "missing event fields"}, status_code=400)

    # 3. Process (idempotent) + reconcile.
    try:
        result = process_event(
            db, event_id=event_id, event_type=event_type, resource=resource
        )
    except Exception:
        # Do not expose internals. 500 lets PayPal retry later.
        logger.warning("webhook_processing_error type=%s", event_type)
        return JSONResponse({"error": "processing error"}, status_code=500)

    return JSONResponse({"status": "ok", "result": result}, status_code=200)
