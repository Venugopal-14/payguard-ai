// Frontend regression checks for the PayPal checkout action.
// Run: npm test  (compiles app/lib/*.ts with the project's TypeScript, then
// uses Node's built-in test runner — no extra dependencies).
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { payActionFor, canStartPayPalCheckout } = require("../.test-build/payAction.js");
const { isSandboxCheckoutUrl } = require("../.test-build/api.js");

const APP = path.join(__dirname, "..", "app");
const read = (rel) => fs.readFileSync(path.join(APP, rel), "utf8");
function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(path.join(dir, e.name)) : [path.join(dir, e.name)]
  );
}
const SOURCES = walk(APP).filter((f) => /\.(ts|tsx)$/.test(f));

// ------------------------------------------------------------ status gating

test("APPROVED shows 'Pay with PayPal (Sandbox)'", () => {
  assert.deepEqual(payActionFor("APPROVED"), { kind: "pay", label: "Pay with PayPal (Sandbox)" });
});

test("PAYPAL_CREATED shows 'Continue to PayPal'", () => {
  assert.deepEqual(payActionFor("PAYPAL_CREATED"), { kind: "continue", label: "Continue to PayPal" });
});

for (const s of ["AWAITING_APPROVAL", "PENDING_APPROVAL", "BLOCKED", "FAILED", "COMPLETED",
                 "PENDING", "approved", "", "toString", "__proto__", null, undefined]) {
  test(`no PayPal action / no redirect for ${JSON.stringify(s)}`, () => {
    assert.equal(payActionFor(s), null);
    assert.equal(canStartPayPalCheckout(s), false);
  });
}

// ------------------------------------------------------------ redirect target

test("only https PayPal Sandbox URLs are accepted for redirect", () => {
  assert.equal(isSandboxCheckoutUrl("https://www.sandbox.paypal.com/checkoutnow?token=5O19"), true);
  assert.equal(isSandboxCheckoutUrl("https://www.paypal.com/checkoutnow?token=X"), false);
  assert.equal(isSandboxCheckoutUrl("http://www.sandbox.paypal.com/checkoutnow?token=X"), false);
  assert.equal(isSandboxCheckoutUrl("https://www.sandbox.paypal.com.evil.example/x"), false);
  assert.equal(isSandboxCheckoutUrl("javascript:alert(1)"), false);
  assert.equal(isSandboxCheckoutUrl(null), false);
});

// ------------------------------------------------------------ wiring (static)

test("one payment implementation: redirect only via lib/api.ts, never window.open", () => {
  for (const f of SOURCES) {
    const src = fs.readFileSync(f, "utf8");
    const rel = path.relative(APP, f).replace(/\\/g, "/");
    assert.ok(!/window\.open\(/.test(src), `${rel} must not open PayPal in a new window`);
    if (rel !== "lib/api.ts") {
      assert.ok(!/location\.(assign|replace|href\s*=)/.test(src),
        `${rel} must not redirect directly; use redirectToPayPal()`);
    }
  }
});

test("button labels live only in lib/payAction.ts (no duplicate hand-rolled buttons)", () => {
  for (const f of SOURCES) {
    const rel = path.relative(APP, f).replace(/\\/g, "/");
    if (rel === "lib/payAction.ts") continue;
    const src = fs.readFileSync(f, "utf8");
    assert.ok(!src.includes("Pay with PayPal (Sandbox)"), `${rel} hard-codes the Pay label`);
    assert.ok(!src.includes("Continue to PayPal"), `${rel} hard-codes the Continue label`);
  }
});

test("payWithPayPal gates on status, calls /execute, then redirects", () => {
  const page = read("page.tsx");
  const fn = page.slice(page.indexOf("async function payWithPayPal"), page.indexOf("async function onExecute"));
  assert.ok(fn.includes("canStartPayPalCheckout(target.status)"));
  assert.ok(fn.indexOf("canStartPayPalCheckout") < fn.indexOf("execute(target.id)"));
  assert.ok(fn.indexOf("execute(target.id)") < fn.indexOf("redirectToPayPal(r.approve_url)"));
  assert.equal((page.match(/redirectToPayPal\(/g) || []).length, 1, "single redirect call site");
});

test("PayButton is wired into Overview, Transactions, Approval Center, Missions and drawer", () => {
  const page = read("page.tsx");
  // Overview + Transactions tables pass onPay to TxTable, which renders PayButton.
  assert.match(page, /<TxTable rows=\{history\.slice\(0, 6\)\}[^>]*onPay=\{onPay\}/);
  assert.match(page, /<TxTable rows=\{rows\}[^>]*onPay=\{onPay\}/);
  const txTable = page.slice(page.indexOf("function TxTable"));
  assert.match(txTable, /<PayButton txn=\{t\}/);
  // Approval Center "ready" section.
  const approval = page.slice(page.indexOf("function ApprovalCenter"), page.indexOf("function TransactionsView"));
  assert.match(approval, /<PayButton txn=\{t\}/);
  assert.match(page, /ready=\{history\.filter\(\(t\) => canStartPayPalCheckout\(t\.status\)\)\}/);
  // Missions page.
  assert.match(page, /<PayButton txn=\{txn\}/);
  // Drawer.
  const drawer = read("components/TransactionDrawer.tsx");
  assert.match(drawer, /canStartPayPalCheckout\(txn\.status\)/);
  assert.match(drawer, /<PayButton txn=\{txn\}/);
});

test("PayButton renders nothing when the status has no action", () => {
  const btn = read("components/PayButton.tsx");
  assert.match(btn, /const action = payActionFor\(txn\.status\);\s*if \(!action\) return null;/);
  assert.match(btn, /e\.stopPropagation\(\)/);
});

test("return page shows the verified result from the backend, not a static success", () => {
  const ret = read("paypal/return/page.tsx");
  assert.match(ret, /paypalReturn\(/);
  assert.ok(!/Payment approved in PayPal ✓/.test(ret));
});
