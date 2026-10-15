"""Admin-panel response schemas (Pydantic v2).

Everything here is served only to admins (see ``require_admin``); still, no
password hashes, tokens, raw IPs or raw user-agents are ever included —
visitors appear as aggregate counts plus short visitor hashes.
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
    last_location: str = ""


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


class TopClick(BaseModel):
    """A frequently pressed button/link: label, page and press count."""

    label: str
    path: str
    count: int


class PageTimeRow(BaseModel):
    """Total dwell time on one path plus how many stays produced it."""

    path: str
    seconds: int
    views: int


class VisitRow(BaseModel):
    """One recent visit: when, where (page + location) and on what device."""

    at: Optional[datetime] = None
    path: str = ""
    email: str = ""
    location: str = ""
    device: str = ""
    visitor: str = ""


class LoginRow(BaseModel):
    """One recent sign-in with its location."""

    email: str = ""
    name: str = ""
    at: Optional[datetime] = None
    location: str = ""


class LiveFeedItem(BaseModel):
    """One fresh action in plain words for the live activity feed."""

    at: Optional[datetime] = None
    text: str = ""
    location: str = ""


class RangeInfo(BaseModel):
    from_date: str
    to_date: str


class AllTime(BaseModel):
    """Lifetime counters (never range-bound — they are states, not events)."""

    users_total: int = 0
    active_users: int = 0
    admin_count: int = 0
    resumes_total: int = 0
    roles_total: int = 0
    total_logins: int = 0
    visits_total: int = 0
    interviews_total: int = 0
    questions_total: int = 0


class InRange(BaseModel):
    """Everything that happened inside the selected date range."""

    signups: int = 0
    logins: int = 0
    interviews: int = 0
    questions: int = 0
    visits: int = 0
    visitors: int = 0
    clicks: int = 0
    seconds: int = 0


class AdminStats(BaseModel):
    range: RangeInfo
    system: SystemStatus
    all_time: AllTime
    in_range: InRange
    signups_by_day: list[DayCount]
    logins_by_day: list[DayCount]
    visits_by_day: list[DayCount]
    interviews_by_day: list[DayCount]
    visitors_by_day: list[DayCount]
    top_pages: list[TopPage]
    top_clicks: list[TopClick]
    page_times: list[PageTimeRow]
    recent_logins: list[LoginRow]
    recent_visits: list[VisitRow]
    live_feed: list[LiveFeedItem]
    recent_users: list[AdminUserRow]


class SetActiveRequest(BaseModel):
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None
