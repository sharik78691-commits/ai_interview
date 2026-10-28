"""Chargeback evidence pack for one payment (admin only).

When a customer disputes a payment, the gateway asks for proof that the
digital service was delivered. This gathers what we already store — the
verified payment, the account, and the sign-ins / interview usage during the
access period — into a JSON summary plus a plain-text document the admin can
upload to the gateway as-is.

Only data we already keep is used (no IP addresses, no transcripts, no card
data — we never have card data).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.activity_log import ANALYZE, LLM_USAGE, LOGIN, PREPARE, ActivityLog
from app.models.billing import Payment, ScreenshotUsage, UsageStatus, UserSubscription
from app.models.user import User
from app.providers.payments.base import to_major_str
from app.services import subscription_service as subs

#: Sign-in times listed individually (the count covers all of them).
MAX_LISTED_LOGINS = 50

POLICY_PATHS = {
    "terms": "/terms",
    "refund_policy": "/refund-policy",
    "privacy_policy": "/privacy-policy",
    "acceptable_use": "/acceptable-use",
}


def _iso(ts: datetime | None) -> str | None:
    ts = subs.aware(ts)
    return ts.isoformat() if ts else None


def _fmt(ts: datetime | None) -> str:
    ts = subs.aware(ts)
    return ts.strftime("%d %b %Y, %H:%M UTC") if ts else "—"


def build_evidence(db: Session, payment_id: int) -> dict | None:
    """Evidence for one payment, or None when it does not exist."""
    p = db.get(Payment, payment_id)
    if p is None:
        return None
    user = db.get(User, p.user_id)
    sub = db.scalar(select(UserSubscription).where(UserSubscription.payment_id == p.id))
    now = subs.utcnow()
    start = subs.aware(sub.starts_at if sub else p.paid_at) or subs.aware(p.created_at)
    end = min(subs.aware(sub.expires_at), now) if sub else now

    def activity(kind: str):
        return (ActivityLog.user_id == p.user_id) & (ActivityLog.kind == kind) \
            & (ActivityLog.created_at >= start) & (ActivityLog.created_at <= end)

    counts = {
        kind: int(db.scalar(select(func.count(ActivityLog.id)).where(activity(kind))) or 0)
        for kind in (LOGIN, PREPARE, ANALYZE, LLM_USAGE)
    }
    logins = db.scalars(
        select(ActivityLog.created_at).where(activity(LOGIN))
        .order_by(ActivityLog.created_at).limit(MAX_LISTED_LOGINS)
    ).all()
    used = db.execute(
        select(func.min(ActivityLog.created_at), func.max(ActivityLog.created_at)).where(
            (ActivityLog.user_id == p.user_id) & ActivityLog.kind.in_((LOGIN, PREPARE, ANALYZE))
            & (ActivityLog.created_at >= start) & (ActivityLog.created_at <= end)
        )
    ).one()
    screenshots = 0
    if sub:
        screenshots = int(db.scalar(
            select(func.coalesce(func.sum(ScreenshotUsage.credits_consumed), 0)).where(
                ScreenshotUsage.subscription_id == sub.id, ScreenshotUsage.status != UsageStatus.FAILED,
            )
        ) or 0)

    site = get_settings().frontend_url.rstrip("/")
    policies = {k: f"{site}{path}" for k, path in POLICY_PATHS.items()}
    data = {
        "generated_at": _iso(now),
        "payment": {
            "id": p.id, "order_id": p.gateway_order_id, "gateway": p.gateway,
            "gateway_payment_id": p.gateway_payment_id, "amount_minor": p.amount_minor,
            "currency": p.currency, "method": p.method, "status": p.status,
            "created_at": _iso(p.created_at), "paid_at": _iso(p.paid_at), "verified_via": p.verified_via,
            "plan_name": p.plan_name, "duration_hours": p.duration_hours or p.duration_days * 24,
            "disputed_at": _iso(p.disputed_at), "dispute_status": p.dispute_status,
            "dispute_reference": p.refund_reference, "dispute_respond_by": _iso(p.dispute_respond_by),
        },
        "customer": {
            "user_id": p.user_id, "email": p.user_email, "name": user.name if user else "",
            "account_created_at": _iso(user.created_at) if user else None,
            "sign_in_method": user.auth_provider if user else "",
            "email_verified": bool(user.email_verified) if user else False,
        },
        "service": {
            "access_starts_at": _iso(start), "access_ends_at": _iso(subs.aware(sub.expires_at) if sub else None),
            "subscription_status": sub.status if sub else None,
            "screenshot_allowance": sub.screenshot_allowance if sub else 0,
            "screenshots_used": screenshots,
            "sign_ins": counts[LOGIN], "interviews_prepared": counts[PREPARE],
            "questions_answered": counts[ANALYZE], "ai_answers_generated": counts[LLM_USAGE],
            "first_activity_at": _iso(used[0]), "last_activity_at": _iso(used[1]),
            "sign_in_times": [_iso(t) for t in logins],
        },
        "policies": policies,
    }
    data["summary_text"] = _summary_text(data, logins)
    return data


def _summary_text(d: dict, logins: list[datetime]) -> str:
    pay, cust, svc, pol = d["payment"], d["customer"], d["service"], d["policies"]
    amount = f"{pay['currency']} {to_major_str(pay['amount_minor'])}"
    lines = [
        "OyeInterview — evidence of service delivered",
        f"Prepared on {_fmt(datetime.fromisoformat(d['generated_at']))}",
        "",
        "1. TRANSACTION",
        f"   Order ID: {pay['order_id']}",
        f"   Gateway payment ID: {pay['gateway_payment_id'] or '—'}",
        f"   Amount: {amount}   Method: {pay['method'] or '—'}",
        f"   Paid at: {_fmt(datetime.fromisoformat(pay['paid_at']) if pay['paid_at'] else None)}",
        f"   Confirmed by: server-side verification with the payment gateway ({pay['verified_via'] or 'n/a'})",
        f"   Product: {pay['plan_name']} — a digital service (online AI interview assistant), "
        f"{pay['duration_hours']} hours of access, activated instantly after payment. "
        "No physical goods are shipped.",
        "",
        "2. CUSTOMER ACCOUNT",
        f"   Email: {cust['email']}" + (f"   Name: {cust['name']}" if cust["name"] else ""),
        f"   Account created: {_fmt(datetime.fromisoformat(cust['account_created_at']) if cust['account_created_at'] else None)}",
        f"   Sign-in method: {cust['sign_in_method'] or '—'}   Email verified: {'yes' if cust['email_verified'] else 'no'}",
        "   The purchase was made while signed in to this account.",
        "",
        "3. SERVICE DELIVERED DURING THE PAID PERIOD",
        f"   Access period: {_fmt(datetime.fromisoformat(svc['access_starts_at']) if svc['access_starts_at'] else None)}"
        f" to {_fmt(datetime.fromisoformat(svc['access_ends_at']) if svc['access_ends_at'] else None)}",
        f"   Sign-ins: {svc['sign_ins']}",
        f"   Interview sessions prepared: {svc['interviews_prepared']}",
        f"   Interview questions answered: {svc['questions_answered']}",
        f"   AI answers generated: {svc['ai_answers_generated']}",
        f"   Screenshots analysed: {svc['screenshots_used']} of {svc['screenshot_allowance']}",
        f"   First use: {_fmt(datetime.fromisoformat(svc['first_activity_at']) if svc['first_activity_at'] else None)}"
        f"   Last use: {_fmt(datetime.fromisoformat(svc['last_activity_at']) if svc['last_activity_at'] else None)}",
    ]
    if logins:
        lines += ["", "   Sign-in times:"] + [f"   - {_fmt(t)}" for t in logins]
        if svc["sign_ins"] > len(logins):
            lines.append(f"   … and {svc['sign_ins'] - len(logins)} more")
    lines += [
        "",
        "4. POLICIES SHOWN AT PURCHASE",
        '   The pricing page states "By purchasing you agree to our Terms, Privacy Policy,',
        '   Refund & Cancellation Policy and Acceptable Use Policy", each linked:',
        f"   Terms: {pol['terms']}",
        f"   Refund & Cancellation Policy: {pol['refund_policy']}",
        f"   Privacy Policy: {pol['privacy_policy']}",
        f"   Acceptable Use Policy: {pol['acceptable_use']}",
        "",
        "Source: OyeInterview server records (payments, subscriptions and activity log).",
    ]
    return "\n".join(lines) + "\n"


__all__ = ["build_evidence"]
