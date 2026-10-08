"""Deterministic risk engine.

Produces a 0-100 risk score and a level (LOW/MEDIUM/HIGH) from signals we can
explain to a human. Nothing here is AI -- it is auditable rules, which is the
whole point of PayGuard: the AI proposes, deterministic controls dispose.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# Merchants we treat as known-good in the sandbox demo. In a real system this
# would be a reputation service / allow-list managed by the user or org.
KNOWN_MERCHANTS = {
    "amazon",
    "apple",
    "best buy",
    "dell",
    "github",
    "google",
    "microsoft",
    "notion",
    "openai",
    "shopify",
    "slack",
    "walmart",
}

# Words that frequently show up in scams / high-risk purchases.
SUSPICIOUS_TERMS = {
    "gift card",
    "crypto",
    "bitcoin",
    "wire transfer",
    "prepaid",
    "untraceable",
    "guaranteed returns",
    "lottery",
}


@dataclass
class RiskResult:
    score: float
    level: str
    signals: list[str] = field(default_factory=list)


def _level_for(score: float) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 35:
        return "MEDIUM"
    return "LOW"


def evaluate_risk(
    *,
    description: str,
    merchant: str,
    amount: float,
    budget: float,
) -> RiskResult:
    score = 0.0
    signals: list[str] = []

    merchant_norm = merchant.strip().lower()
    desc_norm = description.strip().lower()

    # Unknown merchant.
    if merchant_norm not in KNOWN_MERCHANTS:
        score += 30
        signals.append(f"Merchant '{merchant}' is not on the known-merchant list.")
    else:
        signals.append(f"Merchant '{merchant}' is recognized and trusted.")

    # Suspicious terms in the purchase description or merchant.
    hay = f"{desc_norm} {merchant_norm}"
    hit_terms = [t for t in SUSPICIOUS_TERMS if t in hay]
    if hit_terms:
        score += 40
        signals.append("Suspicious terms detected: " + ", ".join(hit_terms) + ".")

    # Spend as a fraction of the remaining budget.
    if budget > 0:
        ratio = amount / budget
        if ratio > 1:
            score += 50
            signals.append(
                f"Amount {amount:.2f} exceeds the mission budget {budget:.2f}."
            )
        elif ratio >= 0.75:
            score += 25
            signals.append(
                f"Amount uses {ratio * 100:.0f}% of the remaining budget."
            )
        elif ratio >= 0.4:
            score += 10
            signals.append(
                f"Amount uses {ratio * 100:.0f}% of the remaining budget."
            )

    # Absolute size heuristic.
    if amount >= 1000:
        score += 15
        signals.append("Large absolute amount (>= 1000).")

    score = min(score, 100.0)
    return RiskResult(score=round(score, 2), level=_level_for(score), signals=signals)
