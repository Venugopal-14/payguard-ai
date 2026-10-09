"""Prometheus metrics for PayGuard.

All metrics are in-process counters/histograms (no DB queries on scrape).
Labels are strictly low-cardinality: never transaction_id, correlation_id,
user input, or PayPal order id. Secrets are never exposed.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

# ---- Transactions / payments ----
transactions_total = Counter(
    "payguard_transactions_total",
    "Transactions created, by decision and status.",
    ["decision", "status"],
)
transaction_value_total = Counter(
    "payguard_transaction_value_total",
    "Cumulative proposed transaction value, by currency.",
    ["currency"],
)

# ---- Risk ----
risk_evaluations_total = Counter(
    "payguard_risk_evaluations_total",
    "Risk evaluations, by resulting level.",
    ["risk_level"],
)
risk_score = Histogram(
    "payguard_risk_score",
    "Distribution of computed risk scores.",
    buckets=(0, 10, 20, 35, 50, 70, 85, 100),
)

# ---- Policy ----
policy_decisions_total = Counter(
    "payguard_policy_decisions_total",
    "Policy decisions, by decision.",
    ["decision"],
)

# ---- Approval ----
approval_requests_total = Counter(
    "payguard_approval_requests_total",
    "Transactions that required human approval.",
)
approval_decisions_total = Counter(
    "payguard_approval_decisions_total",
    "Human approval decisions, by decision.",
    ["decision"],
)

# ---- PayPal ----
paypal_orders_total = Counter(
    "payguard_paypal_orders_total",
    "PayPal order creation attempts, by result.",
    ["result"],
)
paypal_captures_total = Counter(
    "payguard_paypal_captures_total",
    "PayPal capture attempts, by result.",
    ["result"],
)
paypal_latency_seconds = Histogram(
    "payguard_paypal_latency_seconds",
    "PayPal API call latency.",
    ["operation"],
)

# ---- Webhook ----
webhook_events_total = Counter(
    "payguard_webhook_events_total",
    "Webhook events, by type and result.",
    ["event_type", "result"],
)
webhook_duplicates_total = Counter(
    "payguard_webhook_duplicates_total",
    "Duplicate webhook deliveries ignored.",
)
webhook_verification_failures_total = Counter(
    "payguard_webhook_verification_failures_total",
    "Webhook signature verification failures.",
)
webhook_processing_seconds = Histogram(
    "payguard_webhook_processing_seconds",
    "Webhook processing latency.",
)

# ---- Reconciliation ----
reconciliation_total = Counter(
    "payguard_reconciliation_total",
    "Reconciliation outcomes, by result.",
    ["result"],
)

# ---- Audit ----
audit_events_total = Counter(
    "payguard_audit_events_total",
    "Audit events, by actor and event type.",
    ["actor", "event_type"],
)

# ---- API ----
http_requests_total = Counter(
    "payguard_http_requests_total",
    "HTTP requests, by method, path, status.",
    ["method", "path", "status"],
)
http_request_duration_seconds = Histogram(
    "payguard_http_request_duration_seconds",
    "HTTP request latency.",
    ["method", "path"],
)

# ---- AI ----
ai_proposals_total = Counter(
    "payguard_ai_proposals_total",
    "AI proposals, by provider and result.",
    ["provider", "result"],
)
ai_latency_seconds = Histogram(
    "payguard_ai_latency_seconds",
    "AI proposal latency, by provider.",
    ["provider"],
)
