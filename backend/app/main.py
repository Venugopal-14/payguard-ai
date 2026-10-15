import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.payguard import router as payguard_router
from app.api.webhooks import router as webhooks_router
from app.core import metrics
from app.core.config import settings
from app.db.database import Base, engine

# Import models so SQLAlchemy registers the tables before create_all.
from app.models import AuditEvent, Mission, Transaction, WebhookEvent  # noqa: F401

Base.metadata.create_all(bind=engine)


def _ensure_schema() -> None:
    """Lightweight idempotent migration for columns added after a table exists.

    create_all() won't add new columns to pre-existing tables, so add the
    Phase 4B correlation_id column to transactions if it is missing.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        try:
            conn.execute(
                text(
                    "ALTER TABLE transactions "
                    "ADD COLUMN IF NOT EXISTS correlation_id VARCHAR(64)"
                )
            )
        except Exception:
            # SQLite / other backends may not support IF NOT EXISTS; ignore.
            pass


_ensure_schema()

app = FastAPI(title=settings.app_name, version="0.2.0")

_allowed_origins = ["http://localhost:3100", "http://localhost:3000"]
if settings.frontend_url and settings.frontend_url not in _allowed_origins:
    _allowed_origins.append(settings.frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    # Use the route template (low cardinality), not the raw path with ids.
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    try:
        metrics.http_requests_total.labels(
            request.method, path, str(response.status_code)
        ).inc()
        metrics.http_request_duration_seconds.labels(request.method, path).observe(
            time.perf_counter() - start
        )
    except Exception:  # never let metrics break a request
        pass
    return response


app.include_router(payguard_router)
app.include_router(webhooks_router)


@app.get("/metrics")
def prometheus_metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "payguard-api",
        "paypal_configured": settings.paypal_configured,
        "webhook_configured": settings.webhook_configured,
        "ai_configured": settings.ai_configured,
        "ai_provider": settings.ai_provider,
        "api_key_enforced": settings.api_key_enforced,
    }


@app.get("/")
def root():
    return {
        "name": settings.app_name,
        "message": "Let AI act. Never let AI spend blindly.",
    }
