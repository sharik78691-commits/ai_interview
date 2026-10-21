# Payments & Subscriptions

Prepaid, **non-recurring** plans paid through **Razorpay** or **Cashfree**, with
database-driven pricing, user and admin billing views, and a full audit trail.

## Status (2026-10-21)

| Area | Status |
|------|--------|
| Plans in DB, admin CRUD, promo pricing, trials | ✅ Done, tested (SQLite + PostgreSQL 18) |
| Public pricing page reads `/api/plans` | ✅ Done. Admin price edits show without redeploying |
| Checkout → verify → activation (gateway-agnostic) | ✅ Done, end-to-end tested with the mock gateway |
| Razorpay adapter (Orders API, checkout signature, API confirmation, webhooks) | ✅ Implemented and unit-tested against a mocked HTTP transport. ⏳ **Not yet run against Razorpay** (no credentials) |
| Cashfree adapter (PG Orders API, JS SDK redirect, API confirmation, signed webhooks) | ✅ Implemented and unit-tested against a mocked HTTP transport. ⏳ **Not yet run against Cashfree** (no credentials) |
| Auto-renewal / recurring mandates | ❌ Not implemented. Monthly/Quarterly are sold **prepaid** for 30/90 days; all copy says "no auto-renewal" (see *Recurring billing*) |
| Refunds | Issued in the gateway dashboard. Refund / chargeback **webhooks** now update the payment and end the access automatically |
| Feature gating by subscription | ✅ Screenshot analysis only (credits, below). Live interview is not gated (product decision unchanged) |
| 2026-10 catalogue: 5 plans, exact-hour durations, screenshot credits | ✅ Done, tested (SQLite + PostgreSQL 18), applied to the local dev DB |
| INR + USD per-currency prices, admin-managed | ✅ Done. **USD prices are not set** — an admin enters them; USD checkout also needs `*_CURRENCIES` and the gateway's international payments |
| `user_id` ⇒ `user_email` on every table | ✅ Done on the live schema (incl. legacy tables without models), with backfill and a regression test |

## 2026-10 pricing, credits and currencies

### Plans (initial values — all editable in Admin › Billing & pricing)

| Code | Plan | INR | Exact duration | Screenshots | Billing type |
|------|------|----:|----------------|------------:|--------------|
| `1-day` | 1 Day Pass | ₹99 | 24 h | 8 | one_time |
| `3-days` | 3 Days Pass | ₹199 | 72 h | 24 | one_time |
| `7-days` | 7 Days Pass | ₹399 | 168 h | 56 | one_time |
| `1-month` | Monthly Plan | ₹999 | 30 days (720 h) | 240 | recurring (sold prepaid) |
| `3-months` | Quarterly Plan | ₹2,490 | 90 days (2160 h) | 720 | recurring (sold prepaid) |

The four old plans were **updated in place** (same ids, so old payments keep their FK
and their own amount/name snapshots); `1-day` was added. This ran once
(`schema_migrations.name = '2026_10_pricing_v2'`) and never overrides later admin edits.

### Policies

* **Exact periods.** `expires_at = activation instant + duration_hours` (UTC). Never rounded to
  midnight, a calendar month or quarter. Activation = when the payment is verified server-side.
  The UI shows times in the viewer's time zone.
* **Repurchase.** Each purchase is its own `user_subscriptions` row and **starts immediately**
  with its own expiry and credits. Allowances are never merged or reset.
* **Credits.** 1 credit = 1 screenshot image. A request is charged to ONE live subscription — the
  soonest-expiring one that has enough credits. Reserve is a single conditional `UPDATE`
  (`used + n <= allowance`), so concurrent requests can't overspend (CHECK constraint as backstop).
* **Admins** (`users.is_admin`) have unlimited analyses: no plan needed, no subscription charged.
  Their requests are still logged in `screenshot_usage` (`subscription_id` NULL), stay idempotent,
  and keep the 6-per-minute rate limit. The usage block reports `unlimited: true`.
* **Failures.** Rejections before processing (validation, no plan, not enough credits, server not
  configured, rate limit) cost nothing. If the AI call fails after it started, the reservation is
  released. Only completed analyses are charged.
* **Idempotency.** `Idempotency-Key` header on `/api/screenshots/analyze`; `(user_id, request_id)` is
  unique. A completed key is refused (409) and never charged twice; a failed key may be retried.
  Checkout accepts `idempotency_key` (unique per user) so a double click opens one order.
* **Refunds / chargebacks.** Full refund or lost dispute → payment `refunded`, subscription
  `refunded` (access ends, usage history kept). Partial refund → recorded, access kept.
  Dispute opened → payment `disputed`, access kept and flagged. A late "paid" event never re-grants.
* **Manual changes** (grant, extend, cancel, revoke, credit correction) need a reason and write an
  audit row with the admin. Manual grants are `source=manual`, amount 0, no payment: never revenue.
* **Legacy subscriptions** (sold before credits existed) were given 8 credits per day of access,
  the same ratio as the new plans.
* **Email column.** Every table with `user_id` has `user_email`, a snapshot taken when the row is
  created (`app/db/email_snapshot.py`). It is never rewritten when a user changes email; current
  identity always comes from `user_id`. Rows whose user no longer exists keep `''`
  (local DB: 19 `activity_log` + 10 `events` rows) — reported at startup, never invented or deleted.

### Currencies

* `billing_currencies`: `display_enabled` (shown in the pricing selector) and `payments_enabled`.
* `plan_prices`: one row per plan × currency × period (`effective_from/to`); changing an amount
  closes the old row and opens a new one. No exchange rate is stored or applied.
* Checkout in a currency requires: a live price, `payments_enabled`, and a configured gateway whose
  `RAZORPAY_CURRENCIES` / `CASHFREE_CURRENCIES` includes it. Otherwise it is refused — the
  currency is never silently switched. The gateway's reported currency must match or nothing is
  activated.
* Admin revenue is reported **per currency**, never summed.

### Recurring billing

Not implemented. Razorpay Subscriptions can express exact intervals (`period=daily`,
`interval` ≥ 7, so 30 and 90 are possible), but that needs plan objects, customer mandates, renewal
webhooks and live-credential testing, none of which exist yet. Until then `auto_renew` is always
false, the public API returns `auto_renew_available: false`, and the UI says "paid upfront · no
auto-renewal". `plan_prices.provider_refs_json` (e.g. `razorpay_plan_id`) and
`subscription_plans.billing_interval_days` are already stored for that work.

### Desktop app

The `.exe` sends screenshots through its main process, which this change did not touch (desktop
changes need explicit approval). It shows the new credit errors as-is, but it does **not** send an
`Idempotency-Key` yet, so each desktop request gets a server-generated id.

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

**2026-10 migration** (`init_db()` → `_ensure_billing_columns`, `_ensure_user_email_columns`,
then `pricing_service.ensure_catalogue`; all additive and idempotent):

| Change | Objects |
|--------|---------|
| New tables | `plan_prices`, `billing_currencies`, `screenshot_usage`, `schema_migrations` |
| New columns | `subscription_plans`: duration_hours, duration_unit, screenshot_allowance, billing_type, billing_interval_days, is_public · `payments`: duration_hours, screenshot_allowance, idempotency_key, refunded_at, refund_reference · `user_subscriptions`: screenshot_allowance, screenshots_used, billing_type, auto_renew, cancelled_at |
| `user_email` added + backfilled | `activity_log`, `password_reset_tokens`, `subscription_audit_logs`, and legacy tables found in the live DB (`click_events`, `events`, `page_times`, `visit_logs`) |
| Constraints / indexes | `uq_payments_user_idempotency`, `ix_payments_currency_status`, `ix_<table>_user_email`, `ck_user_subscriptions_credits` (PostgreSQL; SQLite gets it on new tables) |
| Data (once) | 5-plan catalogue, INR price rows, INR+USD currencies (USD display-only), credits for legacy subscriptions |

Rollback of the 2026-10 step: restore the pre-deploy `pg_dump`. (Manual alternative: drop the four
new tables and added columns; the old code ignores them, so leaving them in place is also safe.)
Verified: the full suite passes on SQLite; the billing/pricing/screenshot suites pass on
PostgreSQL 18; on the local dev DB every pre-existing table kept its row count.
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
