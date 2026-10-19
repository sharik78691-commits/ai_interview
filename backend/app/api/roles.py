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
from sqlalchemy.orm import Session

from app.auth.dependencies import require_user, require_user_csrf
from app.db.session import get_db
from app.models.role import RoleCreateRequest, RoleDetail, RoleRow, RoleUpdateRequest
from app.models.target_role import TargetRole
from app.models.user import User
from app.repositories.owned_library import OwnedLibraryRepository

logger = logging.getLogger(__name__)

router = APIRouter()

# A library holds at most this many roles per user; saving one more
# auto-deletes the oldest (see OwnedLibraryRepository.prune).
MAX_ROLES_PER_USER = 4
_repo = OwnedLibraryRepository(TargetRole, MAX_ROLES_PER_USER, "Role")


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
    record = _repo.get_owned(db, user_id, role_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Target role not found.")
    return record


@router.post("/roles", response_model=RoleDetail, status_code=201)
def create_role(
    body: RoleCreateRequest,
    user: User = Depends(require_user_csrf),
    db: Session = Depends(get_db),
) -> RoleDetail:
    position = body.position
    job_description = body.job_description
    # The newest save becomes the active role for interviews; older ones stay
    # stored and can be re-selected at any time.
    record = _repo.add_active(
        db,
        TargetRole(
            user_id=user.id,
            user_email=user.email,
            position=position,
            job_description=job_description,
        ),
    )
    logger.info("Target role saved: user=%s role_id=%s", user.id, record.id)
    return _detail(record)


@router.get("/roles", response_model=list[RoleRow])
def list_roles(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[RoleRow]:
    return [_row(r) for r in _repo.list_for_user(db, user.id)]


@router.get("/roles/{role_id}", response_model=RoleDetail)
def get_role(
    role_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> RoleDetail:
    return _detail(_get_owned(db, user.id, role_id))


@router.patch("/roles/{role_id}", response_model=RoleDetail)
def update_role(
    role_id: int,
    body: RoleUpdateRequest,
    user: User = Depends(require_user_csrf),
    db: Session = Depends(get_db),
) -> RoleDetail:
    if (
        body.is_active is None
        and body.position is None
        and body.job_description is None
    ):
        raise HTTPException(status_code=400, detail="Nothing to update.")
    record = _get_owned(db, user.id, role_id)
    if body.is_active is not None:
        _repo.set_active(db, record, body.is_active)
    if body.position is not None:
        record.position = body.position.strip()
    if body.job_description is not None:
        record.job_description = body.job_description
    db.commit()
    db.refresh(record)
    logger.info("Target role updated: user=%s role_id=%s", user.id, record.id)
    return _detail(record)


@router.delete("/roles/{role_id}")
def delete_role(
    role_id: int,
    user: User = Depends(require_user_csrf),
    db: Session = Depends(get_db),
) -> dict:
    # Deleting the active role promotes the newest remaining one.
    _repo.delete(db, _get_owned(db, user.id, role_id))
    logger.info("Target role deleted: user=%s role_id=%s", user.id, role_id)
    return {"message": "Target role deleted."}
