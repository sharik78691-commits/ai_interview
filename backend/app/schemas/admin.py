"""Admin-panel response schemas (Pydantic v2).

Everything here is served only to admins (see ``require_admin``); still, no
password hashes or tokens are ever included. The dashboard is deliberately
lean: business totals + signups + AI usage — no page/click/dwell/geo noise.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class DayCount(BaseModel):
    """One bucket of a per-day series: ``{"day": "2026-10-11", "count": 3}``."""

    day: str
    count: int


class AdminUserRow(BaseModel):
    id: int
    email: str
    name: str = ""
    username: Optional[str] = None
    provider: str = "local"
    is_admin: bool = False
    is_active: bool = True
    login_count: int = 0
    last_login_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class AdminUsersPage(BaseModel):
    total: int
    page: int
    page_size: int
    users: list[AdminUserRow]


class SystemStatus(BaseModel):
    """Backend health for the admin panel — same source as /api/health."""

    demo_mode: bool
    llm_configured: bool
    stt_configured: bool
    google_configured: bool
    database: str
    version: str


class Totals(BaseModel):
    """Lifetime account counters (states, not events)."""

    users_total: int = 0
    active_users: int = 0
    admin_count: int = 0
    total_logins: int = 0


class RangeInfo(BaseModel):
    from_date: str
    to_date: str


class InRange(BaseModel):
    """Event metrics inside the selected date range."""

    signups: int = 0
    interviews: int = 0
    questions: int = 0
    ai_calls: int = 0
    ai_errors: int = 0


class AiUsageRow(BaseModel):
    """Per provider+model AI usage: calls, errors and average latency."""

    provider: str
    model: str
    calls: int
    errors: int
    avg_latency_ms: Optional[float] = None


class AdminStats(BaseModel):
    system: SystemStatus
    range: RangeInfo
    totals: Totals
    in_range: InRange
    signups_by_day: list[DayCount]
    ai_usage: list[AiUsageRow]
    recent_users: list[AdminUserRow]


class SetActiveRequest(BaseModel):
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None


class AIModelUpdateRequest(BaseModel):
    """Admin-editable, non-secret model config. All fields optional.

    ``enabled``/``is_default``/``priority`` are booleans/integer; ``displayName``
    and ``description`` are plain text rendered as-is (never executable).
    """

    enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    free_tier: Optional[bool] = None
    priority: Optional[int] = None
