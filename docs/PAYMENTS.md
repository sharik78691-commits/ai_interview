# Payments & Subscriptions

Prepaid, **non-recurring** plans paid through **Razorpay** or **Cashfree**, with
database-driven pricing, user and admin billing views, and a full audit trail.

## Status (2026-10-19)

| Area | Status |
|------|--------|
| Plans in DB, admin CRUD, promo pricing, trials | ✅ Done, tested (SQLite + PostgreSQL 18) |
| Public pricing page reads `/api/plans` | ✅ Done. Admin price edits show without redeploying |
| Checkout → verify → activation (gateway-agnostic) | ✅ Done, end-to-end tested with the mock gateway |
| Razorpay adapter (Orders API, checkout signature, API confirmation, webhooks) | ✅ Implemented and unit-tested against a mocked HTTP transport. ⏳ **Not yet run against Razorpay** (no credentials) |
| Cashfree adapter (PG Orders API, JS SDK redirect, API confirmation, signed webhooks) | ✅ Implemented and unit-tested against a mocked HTTP transport. ⏳ **Not yet run against Cashfree** (no credentials) |
| Auto-renewal / recurring mandates | ❌ Not implemented on purpose. All copy says "no auto-renewal" |
| Refunds | Manual, in the gateway dashboard. Not automated |
| Feature gating by subscription | ❌ Not enforced (product decision). Helper: `subscription_service.has_active_subscription()` |

## Architecture

```
Angular                          FastAPI                                   Gateways
pricing ──POST /payments/checkout──▶ payment_service.create_checkout ──▶ registry.get_gateway(x).create_order
   │  (plan_id, gateway)            · server computes price (promo-aware)      (razorpay.py / cashfree.py / mock.py)
   │                                · payments row = amount SNAPSHOT
   ▼
CheckoutLauncherService  ── opens Razorpay popup / Cashfree SDK redirect / dev mock
   │  returns gateway's claim
   ▼
POST /payments/{gw}/verify ──▶ verify_client_payment
                               · order must belong to the session user
                               · gateway.verify_client_payment: signature check AND
                                 gateway API lookup (status + amount)
                               · mark_paid: row lock → amount/currency check →
                                 payment PAID → user_subscriptions row (unique per payment)
                                 → subscription_audit_logs "purchase"   — one transaction
Gateway ──POST /payments/webhooks/{gw}──▶ handle_webhook
                               · signature over the RAW body (+ timestamp window for Cashfree)
                               · payment_webhook_events insert; UNIQUE(gateway, event_id) dedupes retries
                               · same mark_paid / mark_failed path
```

* `app/providers/payments/` contains independent gateway adapters behind `PaymentGateway`.
  They do no DB work and grant nothing.
* `app/services/payment_service.py` handles orchestration, idempotency and locking.
* `app/services/subscription_service.py` handles plans, pricing, stacking periods,
  trials, grant/extend/revoke and audit.
* `app/api/billing.py` serves users, the public and webhooks. `app/api/admin_billing.py`
  serves admins.

### Rules enforced

1. The browser never sends a price. Amounts come from the plan, and the gateway must
   report the same amount and currency, or the payment is marked `failed` and nothing
   is activated.
2. A frontend "success" callback is never trusted on its own. Client verification
   re-queries the gateway API.
3. It's idempotent end to end: an already-paid payment returns the existing
   subscription, `user_subscriptions.payment_id` is unique, and webhooks are deduped by
   `(gateway, event_id)`.
4. Older unpaid orders for the same plan are marked `cancelled` when a new checkout
   starts. If one of them is paid later anyway (an old tab), the webhook still honours
   it, so no money is lost.
5. Manual admin grants have `source="manual"`, no payment and amount 0, and each one
   writes an audit row with the admin and a reason.
6. No card number, CVV, UPI PIN or bank data is ever stored. The Cashfree phone
   number is passed to the gateway and not stored.

## Data model (PostgreSQL / SQLite)

| Table | Key columns | Constraints / indexes |
|-------|-------------|-----------------------|
| `subscription_plans` | code, name, price_minor, currency, duration_days, trial_days, promo_* (price/label/start/end), features_json, badge, is_highlighted, is_active, sort_order | `code` unique; CHECKs: price ≥ 0, duration > 0, trial ≥ 0, promo ≥ 0. Never deleted (FK `RESTRICT`) |
| `payments` | user_id → users, user_email, plan_id → plans, plan snapshot (code/name/duration), amount_minor, currency, gateway, receipt, gateway_order_id, gateway_payment_id, status, method, failure_reason, verified_via, is_renewal, created/paid/updated_at | UNIQUE `receipt`; UNIQUE (gateway, gateway_order_id); UNIQUE (gateway, gateway_payment_id); idx (user_id, created_at), (status, created_at), user_email, gateway, plan_id |
| `user_subscriptions` | user_id, user_email, plan_id, plan snapshot, status (active/revoked), source (payment/manual/trial), starts_at, expires_at, payment_id, amount_minor, currency, is_renewal, granted_by_user_id, notes, revoked_at | CHECK expires > starts; UNIQUE payment_id; idx (user_id, expires_at), (status, expires_at), user_email |
| `payment_webhook_events` | gateway, event_id, event_type, gateway_order_id, payment_id, payload, signature_valid, processed_at, processing_error, received_at | UNIQUE (gateway, event_id) |
| `subscription_audit_logs` | user_id, plan_id, subscription_id, payment_id, actor_user_id, action, reason, details_json, created_at | idx (user_id, created_at), action, created_at |

Status `scheduled` / `expired` is **derived from dates at read time**, so no cron job is
needed. Money is integer minor units (paise). Deleting a user cascades their billing rows,
matching the existing resume/role tables.

### Migration

The new tables are created by `init_db()` → `create_all` on the next start (additive;
existing tables and data are untouched). The four plans previously hard-coded on the
pricing page (₹150/3 days, ₹300/7 days, ₹900/30 days, ₹2,000/90 days) are seeded **only
if the plans table is empty**, so admin edits are never overwritten.

Rollback (drops billing data, so take a backup first):
```sql
DROP TABLE subscription_audit_logs, payment_webhook_events, user_subscriptions, payments, subscription_plans;
```
Verified on PostgreSQL 18.6: the full suite (294 tests) passes, and running startup
migration and seeding twice on a populated database changes nothing.

## Going live — checklist

1. **Razorpay** (Dashboard → Account & Settings → API Keys): set `RAZORPAY_KEY_ID` and
   `RAZORPAY_KEY_SECRET`. Use `rzp_test_…` keys first.
   * Webhooks: URL `https://<backend>/api/payments/webhooks/razorpay`; events
     `payment.captured`, `order.paid`, `payment.failed`; set `RAZORPAY_WEBHOOK_SECRET`.
   * Make sure payments are **auto-captured** (dashboard setting). Authorized-but-uncaptured
     payments stay `pending`.
2. **Cashfree** (Merchant Dashboard → Developers): set `CASHFREE_CLIENT_ID`,
   `CASHFREE_CLIENT_SECRET`, `CASHFREE_ENV=sandbox` first.
   * Webhook: `https://<backend>/api/payments/webhooks/cashfree` (orders created by this
     app also send `notify_url`). Signatures are verified with the client secret.
   * Check `CASHFREE_API_VERSION` (default `2023-08-01`) against your dashboard.
3. `FRONTEND_URL` / `BACKEND_URL` must be the public origins (Cashfree `return_url` and
   `notify_url` are built from them).
4. `ENVIRONMENT=production` forces the mock gateway off.
5. Do one real low-value payment per gateway in **test mode**. Check `payments`,
   `user_subscriptions`, `payment_webhook_events` and the admin views, then switch to live keys.
6. Recommended: alerting on `payments.status='failed'` with an "Amount mismatch" reason,
   and on webhook rows with `processing_error`.

## Known limitations

* **Desktop app:** Cashfree's redirect returns to the website's `/billing`, not the
  `file://` app. Recommend buying on the website. Razorpay's popup should work in the
  desktop window but hasn't been tested there.
* **Gateway field names** (Cashfree webhook idempotency header, payment-group labels) follow
  the public docs and need confirming in sandbox. Unknown events are stored and acknowledged
  as `ignored`.
* Refunds and chargebacks don't update `payments` automatically. Record them by revoking
  access in the admin panel with a reason.
* The rate limiter is per process (see ARCHITECTURE §7).
