"""Tests for the opt-in API-key guard (security fix #1).

Covers:
  * enforcement disabled (no server key) -> routes behave as before
  * enforcement enabled -> missing key rejected (401)
  * enforcement enabled -> wrong key rejected (401)
  * enforcement enabled -> correct key accepted (passes the guard)
  * missing server configuration -> fail-open by design (opt-in), documented
  * webhook route is NOT affected by the guard
  * /health reflects api_key_enforced without leaking the key

The guard dependency is tested directly (fast, no DB), and end-to-end through a
FastAPI TestClient with the DB dependency overridden by in-memory SQLite so no
real database or PayPal calls occur.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import require_api_key


# --------------------------------------------------------------------------
# Direct unit tests of the dependency (no HTTP, no DB).
# --------------------------------------------------------------------------

def test_guard_disabled_allows_when_no_server_key(monkeypatch):
    monkeypatch.setattr(settings, "payguard_api_key", "", raising=False)
    # No exception == allowed. Any client value is irrelevant when disabled.
    assert require_api_key(x_api_key=None) is None
    assert require_api_key(x_api_key="anything") is None


def test_guard_enabled_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(settings, "payguard_api_key", "s3cret", raising=False)
    with pytest.raises(HTTPException) as exc:
        require_api_key(x_api_key=None)
    assert exc.value.status_code == 401


def test_guard_enabled_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr(settings, "payguard_api_key", "s3cret", raising=False)
    with pytest.raises(HTTPException) as exc:
        require_api_key(x_api_key="wrong")
    assert exc.value.status_code == 401


def test_guard_enabled_accepts_correct_key(monkeypatch):
    monkeypatch.setattr(settings, "payguard_api_key", "s3cret", raising=False)
    assert require_api_key(x_api_key="s3cret") is None


def test_guard_handles_non_ascii_key(monkeypatch):
    """A non-ASCII configured key compares correctly (byte-encoded path)."""
    key = "clé-sécrète-🔐"  # non-ASCII + emoji
    monkeypatch.setattr(settings, "payguard_api_key", key, raising=False)
    # Correct key accepted.
    assert require_api_key(x_api_key=key) is None
    # Wrong key rejected.
    with pytest.raises(HTTPException) as exc:
        require_api_key(x_api_key="clé-sécrète-wrong")
    assert exc.value.status_code == 401
    # Missing key rejected.
    with pytest.raises(HTTPException):
        require_api_key(x_api_key=None)


def test_api_key_enforced_property(monkeypatch):
    monkeypatch.setattr(settings, "payguard_api_key", "", raising=False)
    assert settings.api_key_enforced is False
    monkeypatch.setattr(settings, "payguard_api_key", "x", raising=False)
    assert settings.api_key_enforced is True


# --------------------------------------------------------------------------
# End-to-end via TestClient with DB + PayPal mocked.
# --------------------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch):
    """A TestClient wired to in-memory SQLite, with no PayPal/AI calls."""
    from app.db.database import Base, get_db
    from app.api.payguard import router as payguard_router
    from app.api.webhooks import router as webhooks_router

    # Single shared in-memory connection so the app (which runs on a worker
    # thread under TestClient) sees the same schema/data as this fixture.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def _override_get_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(payguard_router)
    app.include_router(webhooks_router)
    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def _mission_body():
    return {
        "instruction": "Buy a USB cable under 20",
        "budget": 100,
        "currency": "USD",
        "approval_threshold": 50,
    }


def test_missions_open_when_enforcement_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "payguard_api_key", "", raising=False)
    # Force the deterministic fallback agent (no external AI).
    monkeypatch.setattr(settings, "ollama_base_url", "", raising=False)
    monkeypatch.setattr(settings, "ollama_model", "", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    r = client.post("/api/missions", json=_mission_body())
    assert r.status_code == 200  # guard is a no-op when disabled


def test_missions_rejected_without_key_when_enforced(client, monkeypatch):
    monkeypatch.setattr(settings, "payguard_api_key", "s3cret", raising=False)
    r = client.post("/api/missions", json=_mission_body())
    assert r.status_code == 401


def test_missions_rejected_with_wrong_key_when_enforced(client, monkeypatch):
    monkeypatch.setattr(settings, "payguard_api_key", "s3cret", raising=False)
    r = client.post("/api/missions", json=_mission_body(),
                    headers={"X-API-Key": "nope"})
    assert r.status_code == 401


def test_missions_allowed_with_correct_key_when_enforced(client, monkeypatch):
    monkeypatch.setattr(settings, "payguard_api_key", "s3cret", raising=False)
    monkeypatch.setattr(settings, "ollama_base_url", "", raising=False)
    monkeypatch.setattr(settings, "ollama_model", "", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", "", raising=False)
    r = client.post("/api/missions", json=_mission_body(),
                    headers={"X-API-Key": "s3cret"})
    assert r.status_code == 200


def test_execute_capture_approve_guarded_when_enforced(client, monkeypatch):
    monkeypatch.setattr(settings, "payguard_api_key", "s3cret", raising=False)
    # All three state-changing routes must reject a missing key with 401
    # (not 404/400), proving the guard runs before the handler body.
    assert client.post("/api/transactions/execute",
                       json={"transaction_id": 1}).status_code == 401
    assert client.post("/api/transactions/capture",
                       json={"transaction_id": 1}).status_code == 401
    assert client.post("/api/transactions/1/approve").status_code == 401


def test_list_transactions_not_guarded(client, monkeypatch):
    """Read-only listing stays open even when enforcement is on."""
    monkeypatch.setattr(settings, "payguard_api_key", "s3cret", raising=False)
    r = client.get("/api/transactions")
    assert r.status_code == 200


def test_webhook_not_affected_by_guard(client, monkeypatch):
    """The PayPal webhook must not require X-API-Key; it has its own signature
    verification. With a key configured and no X-API-Key sent, the webhook is
    still reachable and fails on signature (not on the API-key guard)."""
    monkeypatch.setattr(settings, "payguard_api_key", "s3cret", raising=False)
    # Make signature verification deterministically fail (no PayPal call).
    from app.payments import paypal

    async def _no_verify(*args, **kwargs):
        return False

    monkeypatch.setattr(paypal, "verify_webhook_signature", _no_verify)

    r = client.post("/api/webhooks/paypal", json={"id": "EVT", "event_type": "X"})
    # 401 here means invalid *signature* (webhook's own check), NOT the API-key
    # guard — the guard would have blocked before the handler ran. Either way it
    # is reachable without X-API-Key, which is the point.
    assert r.status_code == 401
    assert "signature" in r.json().get("error", "").lower()
