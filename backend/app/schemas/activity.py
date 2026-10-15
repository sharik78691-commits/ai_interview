"""Product-analytics ingestion schemas (Pydantic v2)."""
from pydantic import BaseModel, Field


class PageTimeIn(BaseModel):
    """One dwell-time heartbeat: seconds spent on path."""

    path: str = Field(min_length=1, max_length=500)
    seconds: int = Field(ge=0, le=3600)


class ClickItem(BaseModel):
    """One pressed button/link: visible label only, never input values."""

    path: str = Field(min_length=1, max_length=500)
    label: str = Field(default="", max_length=120)
    target: str = Field(default="", max_length=120)


class ClickBatchIn(BaseModel):
    events: list[ClickItem] = Field(default_factory=list, max_length=50)
