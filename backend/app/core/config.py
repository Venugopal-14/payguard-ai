from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "PayGuard AI"
    environment: str = "development"

    database_url: str = (
        "postgresql+psycopg://payguard:payguard_dev_password@postgres:5432/payguard"
    )

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        # Managed providers (e.g. Render) hand out a 'postgres://' URL, but our
        # SQLAlchemy driver needs the explicit 'postgresql+psycopg://' scheme.
        if v.startswith("postgres://"):
            return "postgresql+psycopg://" + v[len("postgres://"):]
        if v.startswith("postgresql://") and "+psycopg" not in v:
            return "postgresql+psycopg://" + v[len("postgresql://"):]
        return v

    paypal_base_url: str = "https://api-m.sandbox.paypal.com"
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_webhook_id: str = ""

    # Where PayPal redirects the buyer after approval / cancellation.
    frontend_url: str = "http://localhost:3100"

    # AI provider (OpenAI-compatible). Blank => rule-based fallback.
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    # Local Ollama provider (also OpenAI-compatible). If set, used before OpenAI.
    ollama_base_url: str = ""
    ollama_model: str = ""

    # Opt-in API-key guard for state-changing routes. Blank => enforcement OFF
    # (current demo behavior preserved). Set => X-API-Key required on guarded
    # routes. Never a NEXT_PUBLIC_* value; never logged.
    payguard_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    @property
    def paypal_configured(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)

    @property
    def webhook_configured(self) -> bool:
        return bool(self.paypal_webhook_id)

    @property
    def ollama_configured(self) -> bool:
        return bool(self.ollama_base_url and self.ollama_model)

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def ai_configured(self) -> bool:
        """True if any real LLM provider is configured (Ollama or OpenAI)."""
        return self.ollama_configured or self.openai_configured

    @property
    def api_key_enforced(self) -> bool:
        """True when the opt-in API-key guard is active. Never exposes the key."""
        return bool(self.payguard_api_key)

    @property
    def ai_provider(self) -> str:
        if self.ollama_configured:
            return "ollama"
        if self.openai_configured:
            return "openai"
        return "fallback"

    def active_llm(self) -> tuple[str, str, str]:
        """Return (base_url, model, api_key) for the active LLM provider.

        Ollama takes precedence when configured. Ollama ignores the API key but
        the OpenAI-compatible endpoint still expects the header to be present.
        """
        if self.ollama_configured:
            return self.ollama_base_url, self.ollama_model, "ollama"
        return self.openai_base_url, self.openai_model, self.openai_api_key


settings = Settings()
