"""Resume models."""
from pydantic import BaseModel, Field


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
