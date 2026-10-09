"""The PayGuard AI agent.

Given a free-text mission ("Buy a mechanical keyboard under $120 from a trusted
store") the agent proposes a concrete purchase. It uses a real LLM when one is
configured (Ollama first, then any OpenAI-compatible API) and otherwise falls
back to a deterministic heuristic, so the system is always demoable.

SECURITY: the agent only *proposes*. It has no tools that move money, approve,
capture, or alter risk/policy. The deterministic risk + policy engines decide
whether a proposal may proceed. Adversarial instructions in the mission text
cannot change that — the LLM output is validated into a plain data proposal and
nothing more.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import time

import httpx
from pydantic import ValidationError

from app.agent.schemas import AgentProposal
from app.core import metrics
from app.core.config import settings

logger = logging.getLogger("payguard.agent")


@dataclass
class Proposal:
    description: str
    merchant: str
    amount: float
    currency: str
    agent_notes: str
    source: str  # "llm" or "fallback"
    # Richer structured fields (optional; populated by the LLM path).
    intent: str = "purchase"
    category: str = "general"
    confidence: float = 0.0
    provider: str = "fallback"


SYSTEM_PROMPT = (
    "You are PayGuard's purchasing agent. The user gives you a mission with a "
    "budget. Propose exactly ONE concrete purchase that fulfils the mission. "
    "Prefer well-known, trustworthy merchants. You must NEVER exceed the stated "
    "budget. You cannot approve, capture, or execute payments and you cannot "
    "change any spending policy or risk score — you only propose. "
    "Respond ONLY with a compact JSON object with these keys: "
    "intent (string, e.g. 'purchase'), item (string), merchant (string), "
    "amount (number, no currency symbol, <= budget), currency (3-letter code), "
    "category (string), confidence (number 0..1), summary (one short sentence). "
    "Do not include any text outside the JSON object."
)


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of an LLM response."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in LLM response.")
    return json.loads(match.group(0))


async def _propose_with_llm(
    instruction: str, budget: float, currency: str
) -> Proposal:
    base_url, model, api_key = settings.active_llm()
    provider = settings.ai_provider

    user_prompt = (
        f"Mission: {instruction}\n"
        f"Budget: {budget} {currency}\n"
        "Return the JSON object now."
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

    data = _extract_json(content)

    # Validate the model output into a strict structure. Raises on bad data,
    # which the caller catches to fall back deterministically.
    try:
        structured = AgentProposal(**data)
    except ValidationError as exc:
        raise ValueError(f"LLM output failed validation: {exc}") from exc

    # Clamp to the budget regardless of what the model says. The engines would
    # catch an over-budget amount anyway, but we never let the LLM exceed it.
    amount = min(structured.amount, budget)

    summary = structured.summary or (
        f"{structured.intent.title()} {structured.item} from {structured.merchant}"
    )

    return Proposal(
        description=structured.item,
        merchant=structured.merchant,
        amount=round(amount, 2),
        currency=structured.currency or currency,
        agent_notes=summary,
        source="llm",
        intent=structured.intent,
        category=structured.category,
        confidence=structured.confidence,
        provider=provider,
    )


def _propose_with_fallback(
    instruction: str, budget: float, currency: str
) -> Proposal:
    """Deterministic proposal used when no LLM is configured or it fails."""
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", instruction)]
    target = min(nums) if nums else round(budget * 0.8, 2)
    target = min(target, round(budget * 0.95, 2))

    known = [
        "Amazon", "Apple", "Best Buy", "Dell", "GitHub", "Google",
        "Microsoft", "Notion", "OpenAI", "Shopify", "Slack", "Walmart",
    ]
    merchant = "Amazon"
    for name in known:
        if name.lower() in instruction.lower():
            merchant = name
            break

    description = instruction.strip()
    if len(description) > 120:
        description = description[:117] + "..."

    return Proposal(
        description=description or "Mission purchase",
        merchant=merchant,
        amount=round(target, 2),
        currency=currency,
        agent_notes=(
            "Proposed by the rule-based fallback agent. "
            f"Targeted {target:.2f} {currency} against a {budget:.2f} budget."
        ),
        source="fallback",
        intent="purchase",
        category="general",
        confidence=0.5,
        provider="fallback",
    )


async def propose_purchase(
    *, instruction: str, budget: float, currency: str = "USD"
) -> Proposal:
    provider = settings.ai_provider
    if settings.ai_configured:
        logger.info("agent_started provider=%s", provider)
        start = time.perf_counter()
        try:
            proposal = await _propose_with_llm(instruction, budget, currency)
            if proposal.amount <= 0:
                raise ValueError("LLM returned a non-positive amount.")
            metrics.ai_latency_seconds.labels(provider).observe(time.perf_counter() - start)
            metrics.ai_proposals_total.labels(provider, "success").inc()
            logger.info(
                "agent_completed provider=%s merchant=%s amount=%s",
                proposal.provider, proposal.merchant, proposal.amount,
            )
            return proposal
        except Exception as exc:  # noqa: BLE001
            metrics.ai_latency_seconds.labels(provider).observe(time.perf_counter() - start)
            metrics.ai_proposals_total.labels(provider, "fallback").inc()
            logger.warning("agent_failed provider=%s error=%s; using fallback",
                           provider, exc)
            return _propose_with_fallback(instruction, budget, currency)
    metrics.ai_proposals_total.labels("fallback", "success").inc()
    return _propose_with_fallback(instruction, budget, currency)
