"""Admin-editable model flags (LLM and STT share one ``ai_model_config`` table).

Both admin PATCH routes used to duplicate the "upsert row, apply flags, keep
exactly one default" logic. Keys are validated by the caller against the
server allowlist before reaching here, so no arbitrary config is stored.
"""
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_model_config import AIModelConfig
from app.providers.llm import registry
from app.schemas.admin import AIModelUpdateRequest

#: Fields only LLM models expose in the admin panel.
_LLM_ONLY_FIELDS = ("display_name", "description", "free_tier", "priority")


def apply_model_update(
    db: Session,
    key: str,
    body: AIModelUpdateRequest,
    default_scope: Collection[str] | None,
    *,
    allow_catalog_fields: bool,
) -> AIModelConfig:
    """Upsert ``key``'s override row and commit.

    ``default_scope``: keys that compete for "the default" when ``key``
    becomes default (None = every row, i.e. the LLM catalogue; STT passes its
    own keys so the two catalogues never clear each other's default).
    ``allow_catalog_fields``: whether display/description/free-tier/priority
    edits apply (LLM only).
    """
    record = db.get(AIModelConfig, key)
    if record is None:
        record = AIModelConfig(key=key)
        db.add(record)

    if body.enabled is not None:
        record.enabled = body.enabled
    if body.is_default is not None:
        record.is_default = body.is_default
        if body.is_default:
            for other in db.scalars(select(AIModelConfig)).all():
                if other.key != key and (default_scope is None or other.key in default_scope):
                    other.is_default = False
    if allow_catalog_fields:
        if body.display_name is not None:
            record.display_name = body.display_name.strip()[:120]
        if body.description is not None:
            record.description = body.description.strip()[:2000]
        if body.free_tier is not None:
            record.free_tier = body.free_tier
        if body.priority is not None:
            record.priority = max(-1000, min(1000, body.priority))

    db.commit()
    # Cached overrides would otherwise hide this change for up to their TTL;
    # the cache covers the shared table, so both LLM and STT reads refresh.
    registry.invalidate_model_overrides()
    return record


__all__ = ["apply_model_update"]
