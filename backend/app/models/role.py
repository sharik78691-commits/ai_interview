"""Target-role request/response schemas (Pydantic v2)."""
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# Dashboard limits: position is a short title, the JD box caps at 2000 chars.
POSITION_MAX = 200
JD_MAX = 2000


def _strip_non_empty(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ValueError(f"{label} is required.")
    return value


class RoleRow(BaseModel):
    """One role in the user's library (list view)."""

    id: int
    position: str = ""
    chars: int = 0
    is_active: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RoleDetail(RoleRow):
    """Full role with the editable job description."""

    job_description: str = ""


class RoleCreateRequest(BaseModel):
    """Both fields are mandatory (dashboard enforces the same)."""

    position: str = Field(min_length=1, max_length=POSITION_MAX)
    job_description: str = Field(min_length=1, max_length=JD_MAX)

    _position = field_validator("position")(lambda cls, v: _strip_non_empty(v, "Position"))
    _jd = field_validator("job_description")(
        lambda cls, v: _strip_non_empty(v, "Job description")
    )


class RoleUpdateRequest(BaseModel):
    """Select active and/or correct the saved data (caps apply to edits too)."""

    position: str | None = Field(default=None, max_length=POSITION_MAX)
    job_description: str | None = Field(default=None, max_length=JD_MAX)
    is_active: bool | None = None

    _position = field_validator("position")(lambda cls, v: _strip_non_empty(v, "Position"))
    _jd = field_validator("job_description")(
        lambda cls, v: _strip_non_empty(v, "Job description")
    )
