"""Target-role library endpoints (authenticated, per-user).

POST   /api/roles              — save a position + JD (becomes active)
GET    /api/roles              — the caller's role library, newest first
GET    /api/roles/{id}         — full role with job description
PATCH  /api/roles/{id}         — select active and/or correct saved data
DELETE /api/roles/{id}         — delete a saved role

Every row belongs to exactly one user (never shared, never listed across
users); unknown-or-foreign ids answer 404 either way.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_user
from app.db.session import get_db
from app.models.role import RoleCreateRequest, RoleDetail, RoleRow, RoleUpdateRequest
from app.models.target_role import TargetRole
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()

# A library holds at most this many roles per user; saving one more
# auto-deletes the oldest (see _prune_old_roles).
MAX_ROLES_PER_USER = 4


def _row(record: TargetRole) -> RoleRow:
    return RoleRow(
        id=record.id,
        position=record.position or "",
        chars=len(record.job_description or ""),
        is_active=bool(record.is_active),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _detail(record: TargetRole) -> RoleDetail:
    row = _row(record)
    return RoleDetail(
        **row.model_dump(), job_description=record.job_description or ""
    )


def _get_owned(db: Session, user_id: int, role_id: int) -> TargetRole:
    record = db.scalar(
        select(TargetRole).where(
            TargetRole.id == role_id, TargetRole.user_id == user_id
        )
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Target role not found.")
    return record


def _prune_old_roles(db: Session, user_id: int) -> None:
    """Keep only the newest MAX_ROLES_PER_USER rows; auto-delete the rest.

    Runs after every successful save, so the library never grows past the
    limit. If a deleted row was somehow the active one, the newest remaining
    row takes over.
    """
    rows = db.scalars(
        select(TargetRole)
        .where(TargetRole.user_id == user_id)
        .order_by(TargetRole.id.desc())
    ).all()
    doomed = rows[MAX_ROLES_PER_USER:]
    if not doomed:
        return
    active_deleted = any(r.is_active for r in doomed)
    for r in doomed:
        db.delete(r)
    if active_deleted and len(rows) > len(doomed):
        rows[0].is_active = True
    db.commit()
    logger.info(
        "Role auto-prune: user=%s deleted=%d (kept newest %d)",
        user_id, len(doomed), MAX_ROLES_PER_USER,
    )


@router.post("/roles", response_model=RoleDetail, status_code=201)
async def create_role(
    body: RoleCreateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> RoleDetail:
    position = body.position
    job_description = body.job_description
    # The newest save becomes the active role for interviews; older ones stay
    # stored and can be re-selected at any time.
    db.query(TargetRole).filter(TargetRole.user_id == user.id).update(
        {TargetRole.is_active: False}
    )
    record = TargetRole(
        user_id=user.id,
        user_email=user.email,
        position=position,
        job_description=job_description,
        is_active=True,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    _prune_old_roles(db, user.id)
    logger.info("Target role saved: user=%s role_id=%s", user.id, record.id)
    return _detail(record)


@router.get("/roles", response_model=list[RoleRow])
async def list_roles(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[RoleRow]:
    rows = db.scalars(
        select(TargetRole)
        .where(TargetRole.user_id == user.id)
        .order_by(TargetRole.id.desc())
    ).all()
    return [_row(r) for r in rows]


@router.get("/roles/{role_id}", response_model=RoleDetail)
async def get_role(
    role_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> RoleDetail:
    return _detail(_get_owned(db, user.id, role_id))


@router.patch("/roles/{role_id}", response_model=RoleDetail)
async def update_role(
    role_id: int,
    body: RoleUpdateRequest,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> RoleDetail:
    if (
        body.is_active is None
        and body.position is None
        and body.job_description is None
    ):
        raise HTTPException(status_code=400, detail="Nothing to update.")
    record = _get_owned(db, user.id, role_id)
    if body.is_active is True:
        db.query(TargetRole).filter(TargetRole.user_id == user.id).update(
            {TargetRole.is_active: False}
        )
        record.is_active = True
    elif body.is_active is False:
        record.is_active = False
    if body.position is not None:
        record.position = body.position.strip()
    if body.job_description is not None:
        record.job_description = body.job_description
    db.commit()
    db.refresh(record)
    logger.info("Target role updated: user=%s role_id=%s", user.id, record.id)
    return _detail(record)


@router.delete("/roles/{role_id}")
async def delete_role(
    role_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    record = _get_owned(db, user.id, role_id)
    was_active = record.is_active
    db.delete(record)
    db.commit()
    # Deleting the active role must not leave the library without one:
    # promote the newest remaining role.
    if was_active:
        newest = db.scalar(
            select(TargetRole)
            .where(TargetRole.user_id == user.id)
            .order_by(TargetRole.id.desc())
        )
        if newest is not None:
            newest.is_active = True
            db.commit()
    logger.info("Target role deleted: user=%s role_id=%s", user.id, role_id)
    return {"message": "Target role deleted."}
