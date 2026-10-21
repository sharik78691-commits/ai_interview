"""Fill ``user_email`` on every new row that has a ``user_id``.

Schema rule: every table with ``user_id`` also stores ``user_email``. Rather
than repeating "look up the email" in each service, one ``before_flush`` hook
does it for any new ORM object that has both attributes and no email yet.

Policy
------
* The email is resolved from the authoritative ``users`` row — never from
  request data. Callers MAY set it themselves (e.g. from the session user
  they already loaded); a non-empty value is left as is.
* It is a SNAPSHOT taken when the row is created. Later email changes do not
  rewrite historical payments/subscriptions/usage; current identity always
  comes from ``user_id`` → ``users``.
* Rows whose user cannot be resolved keep ``''`` (never an invented value).
"""
from __future__ import annotations

from sqlalchemy import event, select
from sqlalchemy.orm import Session

_installed = False


def _fill_emails(session: Session, _flush_context, _instances) -> None:
    pending = [
        obj for obj in session.new
        if hasattr(obj, "user_email") and getattr(obj, "user_id", None)
        and not getattr(obj, "user_email", None)
    ]
    if not pending:
        return
    from app.models.user import User

    ids = {obj.user_id for obj in pending}
    with session.no_autoflush:
        emails = dict(session.execute(select(User.id, User.email).where(User.id.in_(ids))).all())
    for obj in pending:
        obj.user_email = emails.get(obj.user_id, "") or ""


def install() -> None:
    """Register the hook once for every Session in the process."""
    global _installed
    if _installed:
        return
    event.listen(Session, "before_flush", _fill_emails)
    _installed = True


__all__ = ["install"]
