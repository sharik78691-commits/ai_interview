"""Plan catalogue, currencies and per-currency prices.

* The catalogue (plans, durations, screenshot allowances) and every price
  live in the database; nothing here or in Angular hard-codes a price that
  is served to users. ``DEFAULT_PLANS`` is only the initial seed / one-time
  migration target.
* Each plan has an independent price per currency (``plan_prices``). INR and
  USD are never derived from each other at request time: an admin sets each
  amount explicitly. No exchange rate is stored or assumed.
* Prices are versioned: changing an amount closes the current row and opens
  a new one, so the history of offers is kept. Payments snapshot their own
  amount and currency, so nothing here can rewrite a past transaction.
* A currency can be shown on the pricing page without being payable
  (``payments_enabled``); checkout also requires a configured gateway that
  supports the currency (``registry.gateway_supports``).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.models.billing import (
    BillingCurrency,
    BillingType,
    Payment,
    PlanPrice,
    SchemaMigration,
    SubscriptionPlan,
    UserSubscription,
)
from app.models.user import User
from app.services import subscription_service as subs

logger = logging.getLogger(__name__)

#: Currencies the product supports. Admins toggle display/payments per row.
DEFAULT_CURRENCIES = [
    dict(code="INR", name="Indian Rupee", minor_digits=2, display_enabled=True,
         payments_enabled=True, sort_order=10),
    # Shown so international visitors see the option; payable only once an
    # admin has set USD prices AND enabled USD payments (gateway permitting).
    dict(code="USD", name="US Dollar", minor_digits=2, display_enabled=True,
         payments_enabled=False, sort_order=20),
]

_DEFAULT_FEATURES = [
    "Instant AI answer guidance for interview practice",
    "Resume- and job-description-aware answers",
    "Technical, behavioral and coding questions",
    "Meeting / tab audio transcription",
    "Answer length control (short / medium / long)",
    "Latest AI models, chosen and updated by us",
]

#: One-time copy fix for plans created before 2026-10-22 (wording for payment
#: gateway review). Only exact old strings are replaced, so features an admin
#: wrote or edited are left untouched.
FEATURE_COPY_MIGRATION = "2026_10_feature_copy_v1"
_FEATURE_COPY_FIXES = {
    "Real-time AI answer guidance": "Instant AI answer guidance for interview practice",
    "Choice of available AI models": "Latest AI models, chosen and updated by us",
}

#: Initial catalogue (2026-10). Amounts are INR paise; durations exact hours.
#: Codes 3-days / 7-days / 1-month / 3-months are the rows that already
#: existed, so historical payments keep pointing at the same plan rows.
DEFAULT_PLANS = [
    dict(code="1-day", name="1 Day Pass", price_minor=9_900, duration_hours=24, duration_unit="hours",
         screenshot_allowance=8, billing_type=BillingType.ONE_TIME, sort_order=10,
         description="24-hour access with 8 screenshot analyses. Suitable for a single interview."),
    dict(code="3-days", name="3 Days Pass", price_minor=19_900, duration_hours=72, duration_unit="hours",
         screenshot_allowance=24, billing_type=BillingType.ONE_TIME, sort_order=20,
         description="72-hour access with 24 screenshot analyses. Suitable for short interview preparation."),
    dict(code="7-days", name="7 Days Pass", price_minor=39_900, duration_hours=168, duration_unit="days",
         screenshot_allowance=56, billing_type=BillingType.ONE_TIME, sort_order=30,
         description="7-day access with 56 screenshot analyses. Interview preparation throughout the week."),
    dict(code="1-month", name="Monthly Plan", price_minor=99_900, duration_hours=720, duration_unit="days",
         screenshot_allowance=240, billing_type=BillingType.RECURRING, billing_interval_days=30,
         sort_order=40,
         description="30-day access with 240 screenshot analyses. Suitable for regular interview preparation."),
    dict(code="3-months", name="Quarterly Plan", price_minor=249_000, duration_hours=2160,
         duration_unit="days", screenshot_allowance=720, billing_type=BillingType.RECURRING,
         billing_interval_days=90, sort_order=50,
         description="90-day access with 720 screenshot analyses. Suitable for longer-term preparation."),
]

#: Old 4-plan catalogue codes replaced by the migration below.
_LEGACY_CODES = {"3-days", "7-days", "1-month", "3-months"}
#: Screenshot credits per day of access used to backfill subscriptions sold
#: before credits existed — the same ratio as every new plan (8 per 24h).
LEGACY_CREDITS_PER_DAY = 8
CATALOGUE_MIGRATION = "2026_10_pricing_v2"


# ---------------------------------------------------------------- currencies


def list_currencies(db: Session) -> list[BillingCurrency]:
    return list(db.scalars(select(BillingCurrency).order_by(BillingCurrency.sort_order,
                                                            BillingCurrency.code)).all())


def get_currency(db: Session, code: str) -> BillingCurrency | None:
    return db.get(BillingCurrency, (code or "").upper())


def ensure_currencies(db: Session) -> None:
    """Insert missing default currencies (never changes existing flags)."""
    for spec in DEFAULT_CURRENCIES:
        if db.get(BillingCurrency, spec["code"]) is None:
            db.add(BillingCurrency(**spec))
    db.flush()


# ------------------------------------------------------------------ prices


def _live_price_clause(now: datetime):
    return and_(
        PlanPrice.is_active.is_(True),
        PlanPrice.effective_from <= now,
        or_(PlanPrice.effective_to.is_(None), PlanPrice.effective_to > now),
    )


def current_prices(db: Session, plan_ids: list[int], now: datetime | None = None) -> dict[tuple[int, str], PlanPrice]:
    """``{(plan_id, currency): PlanPrice}`` in effect now (latest start wins)."""
    now = now or subs.utcnow()
    if not plan_ids:
        return {}
    rows = db.scalars(
        select(PlanPrice)
        .where(PlanPrice.plan_id.in_(plan_ids), _live_price_clause(now))
        .order_by(PlanPrice.effective_from.asc(), PlanPrice.id.asc())
    ).all()
    return {(r.plan_id, r.currency): r for r in rows}  # later rows overwrite earlier


@dataclass(frozen=True)
class Quote:
    """What checkout charges for one plan in one currency, right now."""

    amount_minor: int
    currency: str
    is_promo: bool
    regular_minor: int
    price_id: int | None


def quote(
    plan: SubscriptionPlan, currency: str, prices: dict[tuple[int, str], PlanPrice],
    now: datetime | None = None,
) -> Quote | None:
    """Server-side price, or None when the plan has no live price in ``currency``.

    The INR promotion fields on the plan apply only to its primary currency.
    """
    currency = currency.upper()
    row = prices.get((plan.id, currency))
    if row is None:
        return None
    if currency == plan.currency.upper():
        promo = subs.effective_price(plan, now)
        if promo.is_promo and promo.amount_minor < row.amount_minor:
            return Quote(promo.amount_minor, currency, True, row.amount_minor, row.id)
    return Quote(row.amount_minor, currency, False, row.amount_minor, row.id)


def price_history(db: Session, plan_id: int) -> list[PlanPrice]:
    return list(db.scalars(
        select(PlanPrice).where(PlanPrice.plan_id == plan_id)
        .order_by(PlanPrice.currency, PlanPrice.effective_from.desc(), PlanPrice.id.desc())
    ).all())


def set_price(
    db: Session,
    actor: User | None,
    plan: SubscriptionPlan,
    *,
    currency: str,
    amount_minor: int,
    is_active: bool = True,
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
    provider_refs: dict | None = None,
    reason: str = "",
) -> PlanPrice:
    """Set the price of ``plan`` in ``currency`` (caller commits).

    Same amount → the current row's flags/refs/end are updated in place.
    New amount → the current row is closed at the new start and a new row
    opens (history kept). Changing the primary-currency price also updates
    the plan's legacy ``price_minor`` mirror.
    """
    currency = currency.upper()
    cur = get_currency(db, currency)
    if cur is None:
        raise subs.SubscriptionError(f"Currency {currency} is not configured.")
    if amount_minor < 0 or amount_minor > 100_000_000:
        raise subs.SubscriptionError("Price must be between 0 and 1,000,000.00.")
    now = subs.utcnow()
    start = subs.aware(effective_from) or now
    end = subs.aware(effective_to)
    if end is not None and end <= start:
        raise subs.SubscriptionError("Price end must be after its start.")
    live = current_prices(db, [plan.id], now).get((plan.id, currency))
    before = None if live is None else {
        "id": live.id, "amount_minor": live.amount_minor, "is_active": live.is_active,
        "effective_from": subs.aware(live.effective_from), "effective_to": subs.aware(live.effective_to),
    }
    refs = json.dumps(provider_refs) if provider_refs is not None else None
    if live is not None and live.amount_minor == amount_minor and effective_from is None:
        row = live
        row.is_active = is_active
        row.effective_to = end
        if refs is not None:
            row.provider_refs_json = refs
    else:
        # Close every open/future row of this currency that would overlap.
        db.execute(
            update(PlanPrice)
            .where(PlanPrice.plan_id == plan.id, PlanPrice.currency == currency,
                   PlanPrice.effective_from < start,
                   or_(PlanPrice.effective_to.is_(None), PlanPrice.effective_to > start))
            .values(effective_to=start)
            .execution_options(synchronize_session=False)
        )
        row = PlanPrice(plan_id=plan.id, currency=currency, amount_minor=amount_minor,
                        is_active=is_active, effective_from=start, effective_to=end,
                        provider_refs_json=refs or "{}")
        db.add(row)
    db.flush()
    if currency == plan.currency.upper() and start <= now and is_active:
        plan.price_minor = amount_minor
    subs._audit(db, action="price_set", actor_user_id=actor.id if actor else None, plan_id=plan.id,
                reason=reason, details={
                    "currency": currency, "before": before,
                    "after": {"id": row.id, "amount_minor": amount_minor, "is_active": is_active,
                              "effective_from": start, "effective_to": end},
                })
    return row


# --------------------------------------------------------------- catalogue


def _apply_spec(plan: SubscriptionPlan, spec: dict) -> None:
    for key, value in spec.items():
        if key != "code":
            setattr(plan, key, value)
    plan.duration_days = subs.days_for_hours(plan.duration_hours)
    plan.currency = "INR"
    plan.badge = ""
    plan.is_highlighted = False
    plan.is_active = True
    plan.is_public = True
    if spec["billing_type"] == BillingType.ONE_TIME:
        plan.billing_interval_days = None


def _mark(db: Session, name: str) -> bool:
    """True if the migration ``name`` was already applied."""
    return db.get(SchemaMigration, name) is not None


def _ensure_inr_price(db: Session, plan: SubscriptionPlan, reason: str) -> None:
    live = current_prices(db, [plan.id]).get((plan.id, plan.currency.upper()))
    if live is None or live.amount_minor != plan.price_minor:
        set_price(db, None, plan, currency=plan.currency, amount_minor=plan.price_minor, reason=reason)


def ensure_catalogue(db: Session) -> int:
    """Seed or migrate the catalogue exactly once; returns plans created.

    * Empty catalogue → the five default plans with INR prices.
    * Existing catalogue (pre-2026-10) → ONE-TIME migration, recorded in
      ``schema_migrations`` so it never re-runs or overrides later admin
      edits: legacy rows are updated in place to the new plans (same ids,
      so payments keep their FK; payments keep their own snapshots), the
      1 Day Pass is added, INR price rows are created, and subscriptions /
      payments sold before credits existed get 8 credits per day of access.
    USD prices are NOT created — an admin sets them explicitly.
    """
    ensure_currencies(db)
    if _mark(db, CATALOGUE_MIGRATION):
        _migrate_feature_copy(db)
        db.commit()
        return 0
    reason = "Initial catalogue: 5 plans (2026-10 pricing)"
    created = 0
    existing = {p.code: p for p in db.scalars(select(SubscriptionPlan)).all()}
    for spec in DEFAULT_PLANS:
        plan = existing.get(spec["code"])
        if plan is None:
            plan = SubscriptionPlan(code=spec["code"], features_json=json.dumps(_DEFAULT_FEATURES))
            _apply_spec(plan, spec)
            db.add(plan)
            db.flush()
            subs.record_plan_change(db, plan, None, "catalogue_migration", None, reason)
            created += 1
        else:
            before = subs.plan_snapshot(plan)
            _apply_spec(plan, spec)
            db.flush()
            subs.record_plan_change(db, plan, None, "catalogue_migration", before, reason)
    # Any other (admin-made) plan keeps its state; it only gets hours/credits.
    for code, plan in existing.items():
        if code in _LEGACY_CODES or any(s["code"] == code for s in DEFAULT_PLANS):
            continue
        plan.duration_hours = plan.duration_hours or plan.duration_days * 24
        if not plan.screenshot_allowance:
            plan.screenshot_allowance = LEGACY_CREDITS_PER_DAY * subs.days_for_hours(plan.duration_hours)
    db.flush()
    for plan in db.scalars(select(SubscriptionPlan)).all():
        _ensure_inr_price(db, plan, reason)
    # Backfill credits for purchases made before credits existed.
    per_day = LEGACY_CREDITS_PER_DAY
    for sub in db.scalars(select(UserSubscription).where(UserSubscription.screenshot_allowance == 0)):
        hours = (subs.aware(sub.expires_at) - subs.aware(sub.starts_at)).total_seconds() / 3600
        sub.screenshot_allowance = per_day * subs.days_for_hours(int(hours))
    for pay in db.scalars(select(Payment).where(Payment.screenshot_allowance == 0)):
        pay.screenshot_allowance = per_day * subs.days_for_hours(pay.duration_hours or pay.duration_days * 24)
    db.add(SchemaMigration(name=CATALOGUE_MIGRATION))
    _migrate_feature_copy(db)
    db.commit()
    logger.info("Plan catalogue migration %s applied (%d plan(s) created)", CATALOGUE_MIGRATION, created)
    return created


def _migrate_feature_copy(db: Session) -> None:
    """Apply ``_FEATURE_COPY_FIXES`` to every plan once (caller commits)."""
    if _mark(db, FEATURE_COPY_MIGRATION):
        return
    changed = 0
    for plan in db.scalars(select(SubscriptionPlan)).all():
        try:
            features = json.loads(plan.features_json or "[]")
        except ValueError:
            continue
        if not isinstance(features, list):
            continue
        fixed = [_FEATURE_COPY_FIXES.get(f, f) if isinstance(f, str) else f for f in features]
        if fixed != features:
            plan.features_json = json.dumps(fixed)
            changed += 1
    db.add(SchemaMigration(name=FEATURE_COPY_MIGRATION))
    db.flush()
    logger.info("Plan feature copy migration %s applied (%d plan(s) updated)", FEATURE_COPY_MIGRATION, changed)


# ------------------------------------------------------------ public views


def payable_currencies(db: Session) -> set[str]:
    return {c.code for c in list_currencies(db) if c.payments_enabled}


def count_prices(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(PlanPrice.currency, func.count(PlanPrice.id))
        .where(_live_price_clause(subs.utcnow())).group_by(PlanPrice.currency)
    ).all()
    return {c: n for c, n in rows}


__all__ = [
    "CATALOGUE_MIGRATION",
    "FEATURE_COPY_MIGRATION",
    "DEFAULT_CURRENCIES",
    "DEFAULT_PLANS",
    "Quote",
    "count_prices",
    "current_prices",
    "ensure_catalogue",
    "ensure_currencies",
    "get_currency",
    "list_currencies",
    "payable_currencies",
    "price_history",
    "quote",
    "set_price",
]
