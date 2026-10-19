"""Gateway registry — the only place that knows the concrete gateway classes."""
from __future__ import annotations

from app.providers.payments.base import GatewayNotConfigured, PaymentGateway
from app.providers.payments.cashfree import CashfreeGateway
from app.providers.payments.mock import MockGateway
from app.providers.payments.razorpay import RazorpayGateway

# One instance per process (stateless except the mock's in-memory orders).
_GATEWAYS: dict[str, PaymentGateway] = {
    g.name: g for g in (RazorpayGateway(), CashfreeGateway(), MockGateway())
}


def get_gateway(name: str) -> PaymentGateway:
    """The named gateway if it exists AND is configured; else GatewayNotConfigured."""
    gateway = _GATEWAYS.get((name or "").strip().lower())
    if gateway is None or not gateway.configured:
        raise GatewayNotConfigured(f"Payment method '{name}' is not available.")
    return gateway


def available_gateways() -> list[dict[str, str]]:
    """Gateways users may choose right now (configured only; mock in dev only)."""
    return [
        {"key": g.name, "display_name": g.display_name}
        for g in _GATEWAYS.values()
        if g.configured
    ]


def register_gateway(gateway: PaymentGateway) -> None:
    """Replace/add a gateway instance (tests and future providers)."""
    _GATEWAYS[gateway.name] = gateway


__all__ = ["available_gateways", "get_gateway", "register_gateway"]
