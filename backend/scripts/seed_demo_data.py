"""Seed realistic, clearly-labelled DEMO data so the admin dashboard has a story.

Safe by design:
* Only ever ADDS rows for users whose email ends with ``@demo.oyeinterview.test``
  (an unroutable .test domain) - real users/activity are never touched.
* ``--purge`` removes exactly those demo users and their activity again.
* Deterministic (fixed RNG seed) so numbers are reproducible.

Usage (from ``backend/``):
    python -m scripts.seed_demo_data            # add ~120 demo users + 45 days of activity
    python -m scripts.seed_demo_data --purge    # remove all demo data
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.activity_log import ANALYZE, LLM_USAGE, LOGIN, PREPARE, REGISTER, ActivityLog
from app.models.user import User

DEMO_DOMAIN = "@demo.oyeinterview.test"
DAYS = 45
FIRST = ["Aarav", "Vihaan", "Ananya", "Diya", "Rohan", "Ishaan", "Priya", "Kavya", "Arjun", "Neha",
         "Rahul", "Sneha", "Karan", "Meera", "Aditya", "Pooja", "Siddharth", "Riya", "Manish", "Tanvi"]
LAST = ["Sharma", "Verma", "Gupta", "Iyer", "Reddy", "Khan", "Patel", "Nair", "Singh", "Das"]
MODELS = [("groq-gpt-oss", "openai/gpt-oss-120b", 900), ("gemini-flash", "gemini-3.5-flash-lite", 700),
          ("gemini-3.5-flash", "gemini-3.5-flash", 3400), ("deepseek-flash", "deepseek-flash", 1800)]


def _hour(rng: random.Random) -> int:
    """Interview prep clusters late morning and evening (IST) -> store as UTC."""
    ist = rng.choices([9, 10, 11, 14, 18, 19, 20, 21, 22], [3, 5, 4, 3, 5, 7, 8, 6, 3])[0]
    return (ist - 6) % 24  # IST ~ UTC+5:30, close enough for a demo


def purge() -> int:
    db = SessionLocal()
    try:
        ids = list(db.scalars(select(User.id).where(User.email.like(f"%{DEMO_DOMAIN}"))))
        if ids:
            db.execute(delete(ActivityLog).where(ActivityLog.user_id.in_(ids)))
            db.execute(delete(User).where(User.id.in_(ids)))
        db.commit()
        return len(ids)
    finally:
        db.close()


def seed(n_users: int = 120) -> tuple[int, int]:
    Base.metadata.create_all(bind=engine)
    rng = random.Random(20261019)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    db = SessionLocal()
    users_added = events_added = 0
    try:
        existing = set(db.scalars(select(User.email).where(User.email.like(f"%{DEMO_DOMAIN}"))))
        for i in range(n_users):
            email = f"demo{i:03d}{DEMO_DOMAIN}"
            if email in existing:
                continue
            # Sign-ups trend upward over the window (growing business).
            age = int(DAYS * (rng.random() ** 1.6))
            signup = now - timedelta(days=age, hours=rng.randint(0, 23))
            provider = "google" if rng.random() < 0.62 else "local"
            u = User(
                email=email, name=f"{rng.choice(FIRST)} {rng.choice(LAST)}", auth_provider=provider,
                email_verified=True, is_active=True, created_at=signup, updated_at=signup,
                login_count=0,
            )
            db.add(u)
            db.flush()
            users_added += 1

            def add(kind: str, when: datetime, **kw) -> None:
                nonlocal events_added
                db.add(ActivityLog(kind=kind, user_id=u.id, created_at=when, **kw))
                events_added += 1

            add(REGISTER, signup)
            add(LOGIN, signup)
            persona = rng.choices(["ghost", "tryer", "regular", "power"], [28, 32, 28, 12])[0]
            if persona == "ghost":
                u.login_count = 1
                continue
            sessions = {"tryer": 1, "regular": rng.randint(2, 4), "power": rng.randint(5, 9)}[persona]
            qpool = {"tryer": (1, 3), "regular": (2, 6), "power": (5, 14)}[persona]
            logins = 1
            for s in range(sessions):
                day_off = 0 if s == 0 else rng.randint(1, 12) * s
                when = signup + timedelta(days=day_off)
                if when > now:
                    break
                when = when.replace(hour=_hour(rng), minute=rng.randint(0, 59))
                if when > now:
                    when = now - timedelta(minutes=rng.randint(5, 600))
                if s:
                    add(LOGIN, when)
                    logins += 1
                add(PREPARE, when + timedelta(minutes=1))
                for q in range(rng.randint(*qpool)):
                    qt = when + timedelta(minutes=3 + q * rng.randint(2, 6))
                    add(ANALYZE, qt)
                    prov, model, base = rng.choice(MODELS)
                    fail = rng.random() < 0.04
                    add(
                        LLM_USAGE, qt, provider=prov, model=model, request_type="analyze",
                        latency_ms=None if fail else int(base * rng.uniform(0.6, 1.9)),
                        input_tokens=rng.randint(900, 2200), output_tokens=rng.randint(350, 1100),
                        error_category="rate_limit" if fail else "ok",
                    )
            u.login_count = logins
            u.last_login_at = now - timedelta(days=rng.randint(0, 30))
        db.commit()
    finally:
        db.close()
    return users_added, events_added


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--purge", action="store_true", help="remove all demo users + their activity")
    ap.add_argument("--users", type=int, default=120)
    args = ap.parse_args()
    if args.purge:
        print(f"Removed {purge()} demo users (and their activity).")
    else:
        u, e = seed(args.users)
        print(f"Added {u} demo users and {e} activity rows. Remove with --purge.")
