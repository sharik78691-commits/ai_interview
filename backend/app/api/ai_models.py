"""AI model catalog endpoint.

Exposes the server-side model allowlist to the frontend (names, providers,
availability, free-tier status, capabilities) WITHOUT any secrets or raw
endpoint configuration. The frontend can never read API keys from here.
"""
from fastapi import APIRouter

from app.providers.llm import registry

router = APIRouter(tags=["ai"])


@router.get("/ai/models")
async def list_ai_models() -> dict:
    return {"models": registry.list_models(), "default": registry.default_model_key()}
