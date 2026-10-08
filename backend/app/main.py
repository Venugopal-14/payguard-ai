from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.payguard import router as payguard_router
from app.core.config import settings
from app.db.database import Base, engine

# Import models so SQLAlchemy registers the tables before create_all.
from app.models import Mission, Transaction  # noqa: F401

Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.app_name, version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3100", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(payguard_router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "payguard-api",
        "paypal_configured": settings.paypal_configured,
        "ai_configured": settings.ai_configured,
        "ai_provider": settings.ai_provider,
    }


@app.get("/")
def root():
    return {
        "name": settings.app_name,
        "message": "Let AI act. Never let AI spend blindly.",
    }
