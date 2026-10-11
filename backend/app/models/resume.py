"""Resume models."""
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ResumeData(BaseModel):
    name: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    experience: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    raw_text: str = ""


class ResumeUploadResponse(BaseModel):
    filename: str
    chars: int
    resume: ResumeData
    # Storage identity (additive — old clients ignore the new fields).
    id: int = 0
    is_active: bool = False


class ResumeRow(BaseModel):
    """One resume in the user's library (list view — no heavy text)."""

    id: int
    filename: str
    extension: str = ""
    size_bytes: int = 0
    chars: int = 0
    skills: list[str] = Field(default_factory=list)
    is_active: bool = False
    user_email: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ResumeDetail(ResumeRow):
    """Full resume: parsed sections plus editable source text."""

    name: str = ""
    summary: str = ""
    experience: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    raw_text: str = ""


class ResumeUpdateRequest(BaseModel):
    """Select active and/or correct the extracted text (re-parses skills)."""

    is_active: bool | None = None
    raw_text: str | None = Field(default=None, max_length=500_000)


class FromTextRequest(BaseModel):
    """Save pasted/typed editor text as a stored resume (no file needed)."""

    filename: str = Field(default="", max_length=200)
    raw_text: str = Field(min_length=1, max_length=500_000)

    @field_validator("raw_text")
    @classmethod
    def _text_required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Resume text is required.")
        return v

    @field_validator("filename")
    @classmethod
    def _clean_filename(cls, v: str) -> str:
        return (v or "").strip()
