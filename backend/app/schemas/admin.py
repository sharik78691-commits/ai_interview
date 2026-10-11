"""Admin-panel response schemas (Pydantic v2).

Everything here is served only to admins (see ``require_admin``); still, no
password hashes, tokens or raw IPs are ever included — visitors appear only as
aggregate counts.
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
    resumes_count: int = 0
    roles_count: int = 0
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


class TopPage(BaseModel):
    """One of the most-visited paths with its visitor count."""

    path: str
    visits: int
    visitors: int


class AdminStats(BaseModel):
    users_total: int
    active_users: int
    admin_count: int
    total_logins: int
    logins_today: int
    visits_total: int
    visits_today: int
    visitors_today: int
    signups_by_day: list[DayCount]
    visits_by_day: list[DayCount]
    recent_users: list[AdminUserRow]
    # --- Professional panel additions ---
    system: SystemStatus
    interviews_total: int
    interviews_today: int
    questions_total: int
    logins_by_day: list[DayCount]
    interviews_by_day: list[DayCount]
    visitors_by_day: list[DayCount]
    top_pages: list[TopPage]
    recent_logins: list[AdminUserRow]
    resumes_total: int = 0
    roles_total: int = 0


class SetActiveRequest(BaseModel):
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None
