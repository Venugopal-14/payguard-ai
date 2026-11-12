/**
 * Single source of truth for which transactions may start/continue PayPal
 * Sandbox checkout from the UI. Dependency-free so it can be unit-tested
 * (see frontend/tests/payAction.test.cjs).
 *
 * Only two statuses ever get a PayPal action:
 *   APPROVED        -> "Pay with PayPal (Sandbox)"  (human approval done)
 *   PAYPAL_CREATED  -> "Continue to PayPal"         (order exists; resume checkout)
 * Everything else (AWAITING_APPROVAL / "Pending approval", BLOCKED, FAILED,
 * COMPLETED, unknown) gets no action and must never redirect.
 */

export type PayAction = {
  kind: "pay" | "continue";
  label: string;
};

const ACTIONS: Record<string, PayAction> = {
  APPROVED: { kind: "pay", label: "Pay with PayPal (Sandbox)" },
  PAYPAL_CREATED: { kind: "continue", label: "Continue to PayPal" },
};

export function payActionFor(status: string | null | undefined): PayAction | null {
  if (!status) return null;
  return Object.prototype.hasOwnProperty.call(ACTIONS, status) ? ACTIONS[status] : null;
}

export function canStartPayPalCheckout(status: string | null | undefined): boolean {
  return payActionFor(status) !== null;
}
