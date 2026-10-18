"""Business-owner analytics for the admin panel (pure functions, no I/O).

Everything here is derived from data the app already records — ``users`` and
the single ``activity_log`` table — so no new tracking is needed. Aggregation
is done in Python over plain rows so it runs unchanged on SQLite and
PostgreSQL (same approach as ``/api/admin/stats``).

Definitions (kept explicit so the numbers are explainable to a non-engineer):
* **Active user**: any login, interview-prepare or question event.
* **Activated user**: asked at least one interview question.
* **Funnel** is computed for the signup *cohort* inside the date range:
  signed up -> prepared an interview -> asked a question -> came back on a
  second day.
* Admin accounts are excluded everywhere so the owner's own testing never
  inflates business numbers.
* Hour/weekday usage is shown in IST (the business' market).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from app.models.activity_log import ANALYZE, LLM_USAGE, LOGIN, PREPARE

IST = timezone(timedelta(hours=5, minutes=30))
ACTIVE_KINDS = {LOGIN, PREPARE, ANALYZE}

POWER_MIN_QUESTIONS = 10
ENGAGED_MIN_QUESTIONS = 3
LAPSED_AFTER_DAYS = 7


@dataclass(frozen=True)
class UserRow:
    id: int
    name: str
    email: str
    provider: str
    created_at: datetime | None
    is_admin: bool
    is_active: bool


@dataclass(frozen=True)
class EventRow:
    kind: str
    user_id: int | None
    created_at: datetime | None
    provider: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_category: str | None = None


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _day(value: datetime | None) -> date | None:
    v = _utc(value)
    return v.date() if v else None


def _pct(part: int | float, whole: int | float) -> float | None:
    return round(part * 100.0 / whole, 1) if whole else None


def _delta_pct(cur: int | float, prev: int | float) -> float | None:
    """Percent change vs the previous period (None when there is no baseline)."""
    if not prev:
        return None
    return round((cur - prev) * 100.0 / prev, 1)


def _percentile(values: list[int], p: float) -> int | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return s[idx]


def _span(d0: date, d1: date) -> int:
    return (d1 - d0).days + 1


def _kpi(cur: int | float, prev: int | float) -> dict:
    return {"value": cur, "previous": prev, "delta_pct": _delta_pct(cur, prev)}


def build_report(
    users: Iterable[UserRow],
    events: Iterable[EventRow],
    d0: date,
    d1: date,
) -> dict:
    members = [u for u in users if not u.is_admin]
    member_ids = {u.id for u in members}
    by_id = {u.id: u for u in members}
    span = _span(d0, d1)
    p1 = d0 - timedelta(days=1)
    p0 = p1 - timedelta(days=span - 1)

    ev = [e for e in events if e.created_at is not None]
    llm = [e for e in ev if e.kind == LLM_USAGE]
    user_ev = [
        e for e in ev
        if e.user_id in member_ids and e.kind in ACTIVE_KINDS
    ]

    # ---- per-user activity index -------------------------------------
    active_days: dict[int, set[date]] = defaultdict(set)
    last_seen: dict[int, date] = {}
    ever_prepared: set[int] = set()
    ever_asked: set[int] = set()
    q_total: dict[int, int] = defaultdict(int)
    q_range: dict[int, int] = defaultdict(int)
    i_range: dict[int, int] = defaultdict(int)
    for e in user_ev:
        d = _day(e.created_at)
        assert d is not None and e.user_id is not None
        active_days[e.user_id].add(d)
        if e.user_id not in last_seen or d > last_seen[e.user_id]:
            last_seen[e.user_id] = d
        if e.kind == PREPARE:
            ever_prepared.add(e.user_id)
            if d0 <= d <= d1:
                i_range[e.user_id] += 1
        elif e.kind == ANALYZE:
            ever_asked.add(e.user_id)
            q_total[e.user_id] += 1
            if d0 <= d <= d1:
                q_range[e.user_id] += 1

    def active_between(a: date, b: date) -> set[int]:
        return {uid for uid, days in active_days.items() if any(a <= d <= b for d in days)}

    def count_kind(kind: str, a: date, b: date) -> int:
        days = (_day(e.created_at) for e in user_ev if e.kind == kind)
        return sum(1 for d in days if d is not None and a <= d <= b)

    def signups_between(a: date, b: date) -> int:
        days = (_day(u.created_at) for u in members)
        return sum(1 for d in days if d is not None and a <= d <= b)

    # ---- headline KPIs with period-over-period change ------------------
    kpis = {
        "new_users": _kpi(signups_between(d0, d1), signups_between(p0, p1)),
        "active_users": _kpi(len(active_between(d0, d1)), len(active_between(p0, p1))),
        "interviews": _kpi(count_kind(PREPARE, d0, d1), count_kind(PREPARE, p0, p1)),
        "questions": _kpi(count_kind(ANALYZE, d0, d1), count_kind(ANALYZE, p0, p1)),
    }

    # ---- DAU / WAU / MAU anchored on the range end ----------------------
    dau = len(active_between(d1, d1))
    wau = len(active_between(d1 - timedelta(days=6), d1))
    mau = len(active_between(d1 - timedelta(days=29), d1))
    engagement = {
        "dau": dau,
        "wau": wau,
        "mau": mau,
        "stickiness_pct": _pct(dau, mau),
        "questions_per_active_user": (
            round(kpis["questions"]["value"] / kpis["active_users"]["value"], 1)
            if kpis["active_users"]["value"] else None
        ),
    }

    # ---- signup cohort funnel -------------------------------------------
    cohort = [u for u in members if (_day(u.created_at) and d0 <= _day(u.created_at) <= d1)]
    c_ids = {u.id for u in cohort}
    prepared = len(c_ids & ever_prepared)
    asked = len(c_ids & ever_asked)
    returned = sum(1 for uid in c_ids if len(active_days.get(uid, ())) >= 2)
    funnel = [
        {"key": "signed_up", "label": "Signed up", "count": len(cohort)},
        {"key": "prepared", "label": "Started an interview", "count": prepared},
        {"key": "asked", "label": "Asked a question", "count": asked},
        {"key": "returned", "label": "Came back another day", "count": returned},
    ]
    base = funnel[0]["count"]
    for step in funnel:
        step["pct_of_signups"] = _pct(step["count"], base)

    # ---- segments (all non-admin users) ---------------------------------
    anchor_lapsed = d1 - timedelta(days=LAPSED_AFTER_DAYS - 1)
    seg = {"power": 0, "engaged": 0, "casual": 0, "lapsed": 0, "never_activated": 0}
    for u in members:
        q = q_range.get(u.id, 0)
        if q >= POWER_MIN_QUESTIONS:
            seg["power"] += 1
        elif q >= ENGAGED_MIN_QUESTIONS:
            seg["engaged"] += 1
        elif q >= 1:
            seg["casual"] += 1
        elif u.id in ever_asked or u.id in ever_prepared:
            seg["lapsed"] += 1
        else:
            seg["never_activated"] += 1
    segments = [
        {"key": "power", "label": f"Power users ({POWER_MIN_QUESTIONS}+ questions)", "count": seg["power"],
         "hint": "Your best advocates — ask for reviews and referrals, offer a longer plan."},
        {"key": "engaged", "label": f"Engaged ({ENGAGED_MIN_QUESTIONS}-{POWER_MIN_QUESTIONS - 1})", "count": seg["engaged"],
         "hint": "Using it for real interviews — the right moment to show pricing."},
        {"key": "casual", "label": "Casual (1-2)", "count": seg["casual"],
         "hint": "Tried it once or twice — nudge with a tips email."},
        {"key": "lapsed", "label": "Lapsed (used before, quiet in range)", "count": seg["lapsed"],
         "hint": "Win-back email: new feature or a discount."},
        {"key": "never_activated", "label": "Never activated", "count": seg["never_activated"],
         "hint": "Signed up but never asked a question — fix onboarding."},
    ]

    # ---- daily series ----------------------------------------------------
    days = [d0 + timedelta(days=i) for i in range(span)]
    s_signups = {d: 0 for d in days}
    s_active: dict[date, set[int]] = {d: set() for d in days}
    s_questions = {d: 0 for d in days}
    for u in members:
        d = _day(u.created_at)
        if d in s_signups:
            s_signups[d] += 1
    for e in user_ev:
        d = _day(e.created_at)
        if d in s_active:
            s_active[d].add(e.user_id)  # type: ignore[arg-type]
            if e.kind == ANALYZE:
                s_questions[d] += 1
    daily = [
        {"day": d.isoformat(), "signups": s_signups[d], "active": len(s_active[d]), "questions": s_questions[d]}
        for d in days
    ]

    # ---- when do people practise? (weekday x hour, IST) -----------------
    heat = [[0] * 24 for _ in range(7)]
    for e in user_ev:
        if e.kind != ANALYZE:
            continue
        v = _utc(e.created_at)
        assert v is not None
        d = v.date()
        if not (d0 <= d <= d1):
            continue
        local = v.astimezone(IST)
        heat[local.weekday()][local.hour] += 1

    # ---- acquisition mix --------------------------------------------------
    mix: dict[str, int] = defaultdict(int)
    for u in members:
        mix[u.provider or "local"] += 1
    auth_mix = [
        {"provider": p, "count": c, "pct": _pct(c, len(members))}
        for p, c in sorted(mix.items(), key=lambda kv: kv[1], reverse=True)
    ]

    # ---- AI reliability & cost drivers ----------------------------------
    llm_in = [e for e in llm if (_day(e.created_at) and d0 <= _day(e.created_at) <= d1)]
    bad = [e for e in llm_in if e.error_category not in (None, "ok")]
    lat = [e.latency_ms for e in llm_in if e.latency_ms is not None and e.error_category in (None, "ok")]
    ai = {
        "calls": len(llm_in),
        "errors": len(bad),
        "success_rate_pct": _pct(len(llm_in) - len(bad), len(llm_in)),
        "p50_latency_ms": _percentile(lat, 0.5),
        "p95_latency_ms": _percentile(lat, 0.95),
        "input_tokens": sum(e.input_tokens or 0 for e in llm_in),
        "output_tokens": sum(e.output_tokens or 0 for e in llm_in),
        "calls_per_active_user": (
            round(len(llm_in) / kpis["active_users"]["value"], 1)
            if kpis["active_users"]["value"] else None
        ),
    }

    # ---- people lists for action -----------------------------------------
    def person(u: UserRow) -> dict:
        ls = last_seen.get(u.id)
        return {
            "id": u.id, "name": u.name or "", "email": u.email, "provider": u.provider,
            "questions": q_range.get(u.id, 0),
            "questions_total": q_total.get(u.id, 0),
            "interviews": i_range.get(u.id, 0),
            "active_days": len(active_days.get(u.id, ())),
            "last_active": ls.isoformat() if ls else None,
            "days_inactive": (d1 - ls).days if ls else None,
            "signed_up": (_day(u.created_at).isoformat() if _day(u.created_at) else None),
        }

    top_users = [
        person(by_id[uid])
        for uid, _ in sorted(q_range.items(), key=lambda kv: kv[1], reverse=True)[:8]
        if uid in by_id
    ]
    lapsed_valuable = sorted(
        (
            u for u in members
            if q_total.get(u.id, 0) > 0
            and last_seen.get(u.id) is not None
            and last_seen[u.id] <= anchor_lapsed
        ),
        key=lambda u: q_total.get(u.id, 0),
        reverse=True,
    )
    win_back = [person(u) for u in lapsed_valuable[:8]]
    stuck = sorted(
        (u for u in members if u.id not in ever_asked and _day(u.created_at) is not None),
        key=lambda u: _day(u.created_at),  # type: ignore[arg-type,return-value]
        reverse=True,
    )
    never_started = [person(u) for u in stuck[:8]]

    report = {
        "range": {"from_date": d0.isoformat(), "to_date": d1.isoformat(), "days": span,
                  "previous_from": p0.isoformat(), "previous_to": p1.isoformat()},
        "kpis": kpis,
        "engagement": engagement,
        "funnel": funnel,
        "activation_rate_pct": _pct(asked, len(cohort)),
        "retention_rate_pct": _pct(returned, len(cohort)),
        "segments": segments,
        "daily": daily,
        "heatmap": heat,
        "auth_mix": auth_mix,
        "ai": ai,
        "top_users": top_users,
        "win_back": win_back,
        "never_started": never_started,
        "totals": {
            "members": len(members),
            "activated": len(ever_asked & member_ids),
            "never_activated": seg["never_activated"],
            "lapsed_total": len(lapsed_valuable),
        },
    }
    report["insights"] = build_insights(report)
    return report


def build_insights(r: dict) -> list[dict]:
    """Plain-language 'what should I do next' cards, ranked by priority.

    Pure rules over the numbers above — each carries the evidence so the
    owner can verify it rather than trust it blindly.
    """
    out: list[dict] = []

    def add(level: str, title: str, detail: str, action: str) -> None:
        out.append({"level": level, "title": title, "detail": detail, "action": action})

    funnel = {f["key"]: f for f in r["funnel"]}
    signups = funnel["signed_up"]["count"]
    act = r["activation_rate_pct"]
    ret = r["retention_rate_pct"]
    totals = r["totals"]
    ai = r["ai"]
    k = r["kpis"]

    if signups == 0 and totals["members"] == 0:
        add("action", "No customers yet",
            "There are no user sign-ups in the system.",
            "Share the landing page in placement groups, college communities and LinkedIn; "
            "offer the 3-day plan as the entry product.")
        return out

    if signups and act is not None and act < 40:
        add("action", f"Only {act}% of new sign-ups ask a question",
            f"{funnel['asked']['count']} of {signups} sign-ups reached their first answer. "
            "This is the single biggest leak in the funnel.",
            "Add a 60-second guided first interview (pre-filled resume + sample question) right after sign-up.")
    elif act is not None:
        add("good", f"Activation is healthy ({act}%)",
            f"{funnel['asked']['count']} of {signups} new users asked a question.",
            "Keep it up — now focus on getting them to pay: show the pricing page after the 3rd question.")

    if signups and funnel["prepared"]["count"] and funnel["asked"]["count"] < funnel["prepared"]["count"] * 0.7:
        add("warn", "Users start an interview but don't reach an answer",
            f"{funnel['prepared']['count']} prepared an interview, only {funnel['asked']['count']} asked a question.",
            "Check the live-answer screen: mic permission prompts, STT setup and first-answer latency.")

    if ret is not None and signups >= 5 and ret < 25:
        add("warn", f"Low repeat usage ({ret}% return on another day)",
            "Most users try the product once and never come back.",
            "Send a day-2 email with interview-prep tips and a mock-interview mode to build the habit.")

    stick = r["engagement"]["stickiness_pct"]
    if stick is not None and r["engagement"]["mau"] >= 5:
        if stick < 10:
            add("warn", f"Stickiness is {stick}% (daily/monthly users)",
                "Usage is bursty — typical for interview prep, which is event-driven.",
                "Position plans around a job-hunt window (7 days / 1 month) and add reminders before interviews.")
        else:
            add("good", f"Stickiness is {stick}%",
                "A good share of monthly users return daily.", "Consider a referral reward to leverage them.")

    if totals["never_activated"] >= 3:
        add("action", f"{totals['never_activated']} users signed up and never used it",
            "These are free leads you already paid to acquire.",
            "Send a personal onboarding email today (list is in 'Never started' below).")

    if totals["lapsed_total"] >= 3:
        add("action", f"{totals['lapsed_total']} previously active users went quiet",
            f"No activity for {LAPSED_AFTER_DAYS}+ days after asking questions.",
            "Win-back campaign: new-model announcement or a limited-time 3-day plan discount (see 'Win-back' list).")

    seg = {s["key"]: s["count"] for s in r["segments"]}
    if seg.get("power", 0) + seg.get("engaged", 0) >= 1:
        add("good", f"{seg.get('power', 0) + seg.get('engaged', 0)} highly engaged users — your best buyers",
            "People asking 3+ questions are already getting value.",
            "Offer them the 1-month / 3-month plan and ask for a testimonial.")

    if ai["calls"]:
        if ai["success_rate_pct"] is not None and ai["success_rate_pct"] < 95:
            add("warn", f"AI success rate is {ai['success_rate_pct']}%",
                f"{ai['errors']} of {ai['calls']} answer calls failed — each failure is a lost live moment.",
                "Check provider quotas/keys in 'AI models' and enable a fallback model.")
        if ai["p95_latency_ms"] and ai["p95_latency_ms"] > 6000:
            add("warn", f"Slow tail: 95% of answers arrive within {ai['p95_latency_ms'] / 1000:.1f}s",
                "In a live interview, anything over ~5s feels broken.",
                "Make a faster model the default or lower the answer depth for the slowest provider.")

    nu = k["new_users"]
    if nu["delta_pct"] is not None:
        if nu["delta_pct"] >= 15:
            add("good", f"New users up {nu['delta_pct']}% vs previous period",
                f"{nu['value']} vs {nu['previous']}.", "Double down on whatever channel drove this.")
        elif nu["delta_pct"] <= -15:
            add("warn", f"New users down {abs(nu['delta_pct'])}% vs previous period",
                f"{nu['value']} vs {nu['previous']}.", "Restart outreach: LinkedIn posts, campus groups, YouTube demos.")

    mix = {m["provider"]: m for m in r["auth_mix"]}
    if signups == 0 and totals["members"] and "google" not in mix:
        add("warn", "No Google sign-ups", "One-tap Google login usually lifts sign-up conversion.",
            "Enable Google login in settings.")

    order = {"action": 0, "warn": 1, "good": 2}
    out.sort(key=lambda x: order[x["level"]])
    return out
