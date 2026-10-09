"""Deterministic, explainable, multi-factor risk engine (Phase 3A).

The AI proposes; this engine decides risk. Nothing here is AI — every factor is
an auditable rule with a weight and a human-readable description. The LLM can
never set or lower the risk score.

Backward compatibility: `evaluate_risk(description, merchant, amount, budget)`
and `RiskResult.score/.level/.signals` are preserved exactly, so existing
callers, the policy engine, and the API/UI keep working. New structured output
(`factors`, `explanation`, `recommended_action`) is additive, and an optional
`history` argument enables velocity/behavioral factors when available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Levels (CRITICAL is new in Phase 3A).
LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"
CRITICAL = "CRITICAL"

# Recommended actions this engine can suggest (policy engine is still the
# final authority).
ACTION_APPROVE = "APPROVE"
ACTION_REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
ACTION_BLOCK = "BLOCK"


KNOWN_MERCHANTS = {
    "amazon", "apple", "best buy", "dell", "github", "google",
    "microsoft", "notion", "openai", "shopify", "slack", "walmart",
}

# Merchants/categories that are hard-blocked regardless of other factors.
BLOCKED_MERCHANTS = {
    "silkroad", "darkmarket", "unknownvendor",
}
BLOCKED_CATEGORIES = {
    "gambling", "weapons", "adult",
}

SUSPICIOUS_TERMS = {
    "gift card", "crypto", "bitcoin", "wire transfer", "prepaid",
    "untraceable", "guaranteed returns", "lottery",
}

# Lightweight category risk weighting.
CATEGORY_RISK = {
    "electronics": 5,
    "software": 0,
    "general": 5,
    "travel": 10,
    "finance": 20,
    "gift": 30,
    "crypto": 40,
}

ALLOWED_CURRENCIES = {"USD", "EUR", "GBP", "CAD", "AUD", "INR"}

# Velocity window + threshold.
VELOCITY_WINDOW = timedelta(minutes=10)
VELOCITY_THRESHOLD = 3  # more than this many recent txns => velocity risk


@dataclass
class RiskFactor:
    factor: str
    weight: float
    description: str


@dataclass
class RiskResult:
    # Backward-compatible fields (unchanged contract).
    score: float
    level: str
    signals: list[str] = field(default_factory=list)
    # Phase 3A additive fields.
    factors: list[RiskFactor] = field(default_factory=list)
    explanation: str = ""
    recommended_action: str = ACTION_APPROVE


@dataclass
class HistoryRecord:
    """A minimal view of a prior transaction for behavioral/velocity factors."""
    merchant: str
    amount: float
    created_at: datetime
    status: str


def _level_for(score: float) -> str:
    if score >= 85:
        return CRITICAL
    if score >= 70:
        return HIGH
    if score >= 35:
        return MEDIUM
    return LOW


def _action_for(level: str) -> str:
    if level in (HIGH, CRITICAL):
        return ACTION_BLOCK
    if level == MEDIUM:
        return ACTION_REQUIRE_APPROVAL
    return ACTION_APPROVE


def evaluate_risk(
    *,
    description: str,
    merchant: str,
    amount: float,
    budget: float,
    currency: str = "USD",
    category: str = "general",
    history: list[HistoryRecord] | None = None,
) -> RiskResult:
    """Compute an explainable, deterministic risk result.

    `currency`, `category`, and `history` are optional so every existing caller
    keeps working with the original four keyword arguments.
    """
    history = history or []
    factors: list[RiskFactor] = []

    merchant_norm = merchant.strip().lower()
    desc_norm = description.strip().lower()
    category_norm = (category or "general").strip().lower()
    currency_norm = (currency or "USD").strip().upper()

    def add(factor: str, weight: float, description: str) -> None:
        factors.append(RiskFactor(factor=factor, weight=weight, description=description))

    # 1. Hard-blocked merchant.
    if merchant_norm in BLOCKED_MERCHANTS:
        add("blocked_merchant", 100,
            f"Merchant '{merchant}' is explicitly blocked.")

    # 2. Blocked category.
    if category_norm in BLOCKED_CATEGORIES:
        add("blocked_category", 100,
            f"Category '{category_norm}' is explicitly blocked.")

    # 3. Unknown merchant.
    if merchant_norm not in KNOWN_MERCHANTS and merchant_norm not in BLOCKED_MERCHANTS:
        add("unknown_merchant", 30,
            f"Merchant '{merchant}' is not on the known-merchant list.")

    # 4. New merchant (not seen in history) — distinct, lighter signal.
    if history:
        seen = {h.merchant.strip().lower() for h in history}
        if merchant_norm not in seen and merchant_norm in KNOWN_MERCHANTS:
            add("new_merchant", 10,
                f"Merchant '{merchant}' has not been used before.")

    # 5. Suspicious terms.
    hay = f"{desc_norm} {merchant_norm}"
    hit_terms = [t for t in SUSPICIOUS_TERMS if t in hay]
    if hit_terms:
        add("suspicious_terms", 40,
            "Suspicious terms detected: " + ", ".join(hit_terms) + ".")

    # 6. Budget ratio (amount anomaly vs budget).
    if budget > 0:
        ratio = amount / budget
        if ratio > 1:
            add("over_budget", 50,
                f"Amount {amount:.2f} exceeds the budget {budget:.2f}.")
        elif ratio >= 0.75:
            add("high_budget_use", 25,
                f"Amount uses {ratio * 100:.0f}% of the budget.")
        elif ratio >= 0.4:
            add("moderate_budget_use", 10,
                f"Amount uses {ratio * 100:.0f}% of the budget.")

    # 7. Large absolute amount.
    if amount >= 1000:
        add("large_transaction", 15, "Large absolute amount (>= 1000).")

    # 8. Category risk.
    cat_weight = CATEGORY_RISK.get(category_norm, 5)
    if cat_weight > 0:
        add("category_risk", cat_weight,
            f"Category '{category_norm}' carries a baseline risk weight.")

    # 9. Currency anomaly.
    if currency_norm not in ALLOWED_CURRENCIES:
        add("currency_anomaly", 20,
            f"Currency '{currency_norm}' is outside the allowed set.")

    # 10. Transaction velocity (recent activity).
    if history:
        now = datetime.utcnow()
        recent = [h for h in history if now - h.created_at <= VELOCITY_WINDOW]
        if len(recent) > VELOCITY_THRESHOLD:
            add("velocity_anomaly", 20,
                f"{len(recent)} transactions in the last "
                f"{int(VELOCITY_WINDOW.total_seconds() // 60)} minutes.")

    # 11. Behavioral baseline (amount vs the user's historical average).
    completed = [h for h in history if h.status == "COMPLETED"]
    if len(completed) >= 3:
        avg = sum(h.amount for h in completed) / len(completed)
        if avg > 0 and amount > avg * 4:
            add("behavioral_anomaly", 20,
                f"Amount {amount:.2f} is far above the historical average "
                f"{avg:.2f}.")

    # 12. Repeated recent failures.
    failed = [h for h in history if h.status == "FAILED"]
    if len(failed) >= 2:
        add("repeated_failures", 15,
            f"{len(failed)} failed transactions in recent history.")

    score = min(sum(f.weight for f in factors), 100.0)
    level = _level_for(score)
    recommended_action = _action_for(level)

    # Backward-compatible signals = factor descriptions. If nothing fired, note
    # that the transaction looked clean so the UI still shows something useful.
    signals = [f.description for f in factors]
    if not signals:
        signals.append("No elevated risk signals detected.")

    explanation = _build_explanation(factors, level)

    return RiskResult(
        score=round(score, 2),
        level=level,
        signals=signals,
        factors=factors,
        explanation=explanation,
        recommended_action=recommended_action,
    )


def _build_explanation(factors: list[RiskFactor], level: str) -> str:
    if not factors:
        return "No elevated risk factors were detected; the transaction looks normal."
    top = sorted(factors, key=lambda f: f.weight, reverse=True)[:3]
    reasons = ", ".join(f.factor.replace("_", " ") for f in top)
    return (
        f"Risk level {level}. Primary contributing factors: {reasons}."
    )
