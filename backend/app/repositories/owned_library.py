"""Data access for per-user "libraries" (resumes, target roles).

Both libraries follow the same rules, which used to be copy-pasted into each
router:

* every row belongs to exactly one user (``user_id``), and every query is
  scoped by it, so an unknown id and someone else's id look the same (None);
* at most ``max_items`` rows per user; storing one more prunes the oldest;
* exactly one row per user is ``is_active``; deleting or pruning the active
  row promotes the newest remaining one.

The repository works with any mapped class that has ``id``, ``user_id`` and
``is_active`` columns. Callers own the transaction boundaries only where noted.
"""
from __future__ import annotations

import logging
from typing import Generic, Protocol, TypeVar

from sqlalchemy import select, update
from sqlalchemy.orm import InstrumentedAttribute, Session

logger = logging.getLogger(__name__)


class OwnedRow(Protocol):
    id: int
    user_id: int
    is_active: bool


T = TypeVar("T", bound=OwnedRow)


class OwnedLibraryRepository(Generic[T]):
    def __init__(self, model: type[T], max_items: int, label: str) -> None:
        self.model = model
        self.max_items = max_items
        self.label = label

    # ------------------------------------------------------------ columns
    @property
    def _id(self) -> InstrumentedAttribute:
        return self.model.id  # type: ignore[return-value]

    @property
    def _user_id(self) -> InstrumentedAttribute:
        return self.model.user_id  # type: ignore[return-value]

    # ------------------------------------------------------------- queries
    def get_owned(self, db: Session, user_id: int, item_id: int) -> T | None:
        return db.scalar(
            select(self.model).where(self._id == item_id, self._user_id == user_id)
        )

    def list_for_user(self, db: Session, user_id: int) -> list[T]:
        """Newest first."""
        return list(
            db.scalars(
                select(self.model).where(self._user_id == user_id).order_by(self._id.desc())
            ).all()
        )

    def newest(self, db: Session, user_id: int) -> T | None:
        return db.scalar(
            select(self.model).where(self._user_id == user_id).order_by(self._id.desc())
        )

    # ----------------------------------------------------------- mutations
    def deactivate_all(self, db: Session, user_id: int) -> None:
        """Clear ``is_active`` on every row of the user (no commit)."""
        db.execute(
            update(self.model)
            .where(self._user_id == user_id)
            .values(is_active=False)
            .execution_options(synchronize_session="fetch")
        )

    def add_active(self, db: Session, item: T) -> T:
        """Store ``item`` as the user's active row, then enforce the limit."""
        self.deactivate_all(db, item.user_id)
        item.is_active = True
        db.add(item)
        db.commit()
        db.refresh(item)
        self.prune(db, item.user_id)
        return item

    def set_active(self, db: Session, item: T, active: bool) -> None:
        """Select (or unselect) ``item``; selecting clears the others (no commit)."""
        if active:
            self.deactivate_all(db, item.user_id)
        item.is_active = active

    def prune(self, db: Session, user_id: int) -> int:
        """Keep the newest ``max_items`` rows; returns how many were deleted."""
        rows = self.list_for_user(db, user_id)
        doomed = rows[self.max_items:]
        if not doomed:
            return 0
        active_deleted = any(r.is_active for r in doomed)
        for r in doomed:
            db.delete(r)
        if active_deleted and len(rows) > len(doomed):
            rows[0].is_active = True
        db.commit()
        logger.info(
            "%s auto-prune: user=%s deleted=%d (kept newest %d)",
            self.label, user_id, len(doomed), self.max_items,
        )
        return len(doomed)

    def delete(self, db: Session, item: T) -> None:
        """Delete ``item``; if it was active, promote the newest remaining row."""
        user_id, was_active = item.user_id, item.is_active
        db.delete(item)
        db.commit()
        if was_active:
            newest = self.newest(db, user_id)
            if newest is not None:
                newest.is_active = True
                db.commit()


__all__ = ["OwnedLibraryRepository"]
