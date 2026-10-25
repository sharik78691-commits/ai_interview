"""Site-wide payment switches: online payments on/off + which gateways are on.

The admin can switch online payments off for the whole site, turn Cashfree
and Razorpay on or off independently (both on = the customer picks one at
checkout) and choose the DEFAULT gateway that is pre-selected. Everything
that offers or starts a checkout asks :func:`checkout_gateways` /
:func:`assert_checkout_allowed` so the pricing page, the public gateway list
and ``POST /payments/checkout`` always agree.

What this deliberately does NOT block: verifying, reconciling and webhooks
for orders that already exist. Money a customer has already paid is always
honoured through the gateway that created the order.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.billing import SELECTABLE_GATEWAYS, PaymentSettings
from app.models.user import User
from app.providers.payments.registry import available_gateways
from app.services import subscription_service as subs

#: Used until an admin saves the settings (matches the pre-setting behaviour).
DEFAULT_GATEWAY = "cashfree"

UNAVAILABLE_MESSAGE = "Online payments are temporarily unavailable. Please try again later."

_NAMES = {"cashfree": "Cashfree", "razorpay": "Razorpay"}


class PaymentsDisabled(ValueError):
    """Online payments are switched off site-wide (shown to the user)."""


class GatewayNotSelectable(ValueError):
    """The requested gateway / change is not allowed."""


def _defaults() -> PaymentSettings:
    return PaymentSettings(id=1, payments_enabled=True, active_gateway=DEFAULT_GATEWAY,
                           cashfree_enabled=True, razorpay_enabled=False)


def get(db: Session) -> PaymentSettings:
    """The settings row, or unsaved defaults when none exists yet."""
    return db.get(PaymentSettings, 1) or _defaults()


def _is_on(settings: PaymentSettings, key: str) -> bool:
    return bool(getattr(settings, f"{key}_enabled", False))


def enabled_gateways(settings: PaymentSettings) -> list[str]:
    """Gateways the admin switched on, default first."""
    on = [k for k in SELECTABLE_GATEWAYS if _is_on(settings, k)]
    return sorted(on, key=lambda k: k != settings.active_gateway)


def checkout_gateways(db: Session) -> list[dict]:
    """Configured gateways a NEW checkout may use right now, default first.

    Empty when payments are off. Otherwise every admin-enabled gateway whose
    keys are configured, plus the dev-only mock gateway when enabled.
    """
    settings = get(db)
    if not settings.payments_enabled:
        return []
    order = enabled_gateways(settings) + ["mock"]
    configured = {g["key"]: g for g in available_gateways()}
    return [configured[k] for k in order if k in configured]


def assert_checkout_allowed(db: Session, gateway_name: str) -> None:
    """Raise unless a new checkout through ``gateway_name`` is allowed now."""
    if not get(db).payments_enabled:
        raise PaymentsDisabled(UNAVAILABLE_MESSAGE)
    key = (gateway_name or "").strip().lower()
    if key not in {g["key"] for g in checkout_gateways(db)}:
        raise GatewayNotSelectable("This payment method is not available right now.")


def status(db: Session) -> dict:
    """Public view: is checkout possible, and through which gateways."""
    settings = get(db)
    gateways = checkout_gateways(db)
    real = [g["display_name"] for g in gateways if g["key"] != "mock"]
    available = bool(gateways)
    return {
        "payments_enabled": settings.payments_enabled,
        "active_gateway": settings.active_gateway,
        "active_gateway_name": real[0] if real else "",
        "gateway_names": real,
        "available": available,
        "message": "" if available else UNAVAILABLE_MESSAGE,
    }


def admin_view(db: Session) -> dict:
    """Admin view: settings + every selectable gateway (enabled / configured / default)."""
    settings = get(db)
    configured = {g["key"]: g for g in available_gateways()}
    return {
        "payments_enabled": settings.payments_enabled,
        "active_gateway": settings.active_gateway,
        "checkout_available": bool(checkout_gateways(db)),
        "updated_at": settings.updated_at,  # None until an admin first saves
        "updated_by_user_id": settings.updated_by_user_id,
        "gateways": [
            {
                "key": key,
                "display_name": configured.get(key, {}).get("display_name") or _NAMES[key],
                "enabled": _is_on(settings, key),
                "is_default": key == settings.active_gateway,
                "configured": key in configured,
                "currencies": configured.get(key, {}).get("currencies", []),
            }
            for key in SELECTABLE_GATEWAYS
        ],
    }


def _check_key(key: str) -> str:
    key = (key or "").strip().lower()
    if key not in SELECTABLE_GATEWAYS:
        raise GatewayNotSelectable(f"Unknown payment gateway '{key}'.")
    return key


def update(
    db: Session,
    admin: User,
    *,
    payments_enabled: bool | None = None,
    active_gateway: str | None = None,
    gateways_enabled: dict[str, bool] | None = None,
    reason: str = "",
) -> dict:
    """Apply an admin change (audited).

    A gateway can be switched on/selected even before its keys are set on the
    server — it simply is not offered at checkout until they are (the admin
    page warns). Rules that keep the settings consistent:

    * making a gateway the default also switches it on;
    * switching off the default moves the default to another enabled gateway;
    * at least one gateway stays on (use ``payments_enabled`` to stop payments).
    """
    row = db.get(PaymentSettings, 1)
    if row is None:
        row = _defaults()
        db.add(row)
    before = {"payments_enabled": row.payments_enabled, "active_gateway": row.active_gateway,
              "enabled": enabled_gateways(row)}
    for key, on in (gateways_enabled or {}).items():
        setattr(row, f"{_check_key(key)}_enabled", bool(on))
    if active_gateway is not None:
        key = _check_key(active_gateway)
        row.active_gateway = key
        setattr(row, f"{key}_enabled", True)
    on = [k for k in SELECTABLE_GATEWAYS if _is_on(row, k)]
    if not on:
        raise GatewayNotSelectable(
            "Keep at least one gateway on. To stop all payments, turn off site-wide online payments instead."
        )
    if row.active_gateway not in on:
        row.active_gateway = on[0]
    if payments_enabled is not None:
        row.payments_enabled = bool(payments_enabled)
    row.updated_by_user_id = admin.id
    after = {"payments_enabled": row.payments_enabled, "active_gateway": row.active_gateway,
             "enabled": enabled_gateways(row)}
    subs._audit(db, action="payment_settings", actor_user_id=admin.id, reason=reason,
                details={"before": before, "after": after})
    db.commit()
    return admin_view(db)


__all__ = [
    "DEFAULT_GATEWAY", "GatewayNotSelectable", "PaymentsDisabled", "UNAVAILABLE_MESSAGE",
    "admin_view", "assert_checkout_allowed", "checkout_gateways", "enabled_gateways", "get",
    "status", "update",
]
