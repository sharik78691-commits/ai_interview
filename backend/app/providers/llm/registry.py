"""Server-side LLM model allowlist + provider factory.

Every request that names a model is validated against this registry — a client
can never make the backend call an arbitrary model or endpoint. Model keys are
short, stable slugs; the exact provider ``model_id`` and endpoint live ONLY here
(and in env vars), never in the browser.

Two layers of configuration, merged here:

1. **Immutable, secret-bound** (env): provider model id, endpoint, API key.
2. **Admin-editable, non-secret** (``ai_model_config`` table): enabled flag,
   default marker, display name, description, free-tier label, priority.

A model is *available* only when its provider key is configured AND the admin
has not disabled it. Secrets are read from the backend environment only.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

from app.core.config import get_settings
from app.providers.llm.provider import (
    LLMProvider,
    MockLLMProvider,
    OpenAICompatibleLLMProvider,
)

DEFAULT_KEY = "groq-gpt-oss"


@dataclass(frozen=True)
class ModelSpec:
    """One allowed model (env catalog only — DB overrides merged separately)."""

    key: str
    provider: str
    display_name: str
    model_id: str
    base_url: str
    api_key: str
    free_tier: bool
    capabilities: str


# Canonical model catalog. model_id values were verified against the providers'
# public model lists / pricing pages (see README — "AI model selection"). The
# base_urls are OpenAI-compatible chat-completions endpoints, so one client
# serves all four.
def _catalog() -> list[ModelSpec]:
    s = get_settings()
    return [
        ModelSpec(
            key="groq-gpt-oss",
            provider="Groq",
            display_name="GPT-OSS 120B — Groq",
            model_id=s.llm_model,
            base_url="https://api.groq.com/openai/v1",
            api_key=s.groq_api_key,
            free_tier=True,
            capabilities="Fast, strong open-weight model for technical and coding answers",
        ),
        ModelSpec(
            key="gemini-flash",
            provider="Google",
            display_name="Gemini Flash — Google",
            model_id=s.gemini_model,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            api_key=s.gemini_api_key,
            free_tier=True,
            capabilities="Google's low-latency Flash model via the official OpenAI-compatible endpoint",
        ),
        ModelSpec(
            key="qwen-coder",
            provider="OpenRouter · Qwen",
            display_name="Qwen — OpenRouter",
            model_id=s.qwen_model,
            base_url="https://openrouter.ai/api/v1",
            api_key=s.openrouter_api_key,
            free_tier=True,
            capabilities="Qwen coder model (free tier) — strong on code and long context",
        ),
        ModelSpec(
            key="openrouter-free",
            provider="OpenRouter",
            display_name="Free model — OpenRouter",
            model_id=s.openrouter_model,
            base_url="https://openrouter.ai/api/v1",
            api_key=s.openrouter_api_key,
            free_tier=True,
            capabilities="A general free open-weight model as a fallback option",
        ),
    ]


# Admin-override cache: every answer generation reads overrides twice
# (default resolution + enabled check). TTL + write-invalidation below.
_OVERRIDES_TTL_S = 15.0
_overrides_cache: dict | None = None
_overrides_cached_at: float = 0.0


def _load_overrides() -> dict:
    """Read admin model overrides from the DB (empty on any failure — the
    feature degrades gracefully to the env catalog).

    Cached briefly so a DB round-trip isn't pure latency on every answer.
    Admin writes invalidate the cache immediately, so the worst-case
    staleness is the TTL above.
    """
    global _overrides_cache, _overrides_cached_at
    now = time.monotonic()
    if _overrides_cache is not None and (now - _overrides_cached_at) < _OVERRIDES_TTL_S:
        return _overrides_cache
    try:
        from app.db.session import SessionLocal
        from app.models.ai_model_config import AIModelConfig

        db = SessionLocal()
        try:
            result = {r.key: r for r in db.query(AIModelConfig).all()}
        finally:
            db.close()
    except Exception:
        return _overrides_cache if _overrides_cache is not None else {}
    _overrides_cache = result
    _overrides_cached_at = now
    return result


def invalidate_model_overrides() -> None:
    """Drop the cached admin overrides (called on every admin write)."""
    global _overrides_cache, _overrides_cached_at
    _overrides_cache = None
    _overrides_cached_at = 0.0


def default_model_key() -> str:
    """Resolve the default model: DB override -> env -> built-in Groq default."""
    overrides = _load_overrides()
    for key, ov in overrides.items():
        if ov.is_default:
            return key
    env_default = os.getenv("DEFAULT_LLM_MODEL", "").strip()
    if env_default in {m.key for m in _catalog()}:
        return env_default
    return DEFAULT_KEY


def get_catalog() -> dict[str, ModelSpec]:
    return {m.key: m for m in _catalog()}


def get_model(key: str | None = None) -> ModelSpec:
    """Return the ModelSpec for ``key`` (default when omitted).

    Raises ``ValueError`` for an unknown key.
    """
    catalog = get_catalog()
    resolved = (key or "").strip() or default_model_key()
    spec = catalog.get(resolved)
    if spec is None:
        raise ValueError(f"unknown model '{resolved}'")
    return spec


def is_enabled(key: str) -> bool:
    ov = _load_overrides().get(key)
    return ov.enabled if ov else True


def model_available(key: str | None = None) -> bool:
    """True when the model has an API key AND is not admin-disabled."""
    try:
        spec = get_model(key)
    except ValueError:
        return False
    return bool(spec.api_key) and is_enabled(spec.key)


def validate_model_key(key: str | None) -> str:
    """Validate a client-supplied model key, returning its canonical form.

    ``None``/empty → default model. Unknown key → ``ValueError`` (the API layer
    turns that into a 4xx so the user is told, not silently switched).
    """
    if key is None or not key.strip():
        return default_model_key()
    return get_model(key).key


def list_models() -> list[dict]:
    """Public model list for the frontend/admin. No secrets, only availability."""
    overrides = _load_overrides()
    default_key = default_model_key()
    result = []
    for m in _catalog():
        ov = overrides.get(m.key)
        enabled = ov.enabled if ov else True
        free_tier = m.free_tier if (ov is None or ov.free_tier is None) else bool(ov.free_tier)
        display = (ov.display_name or m.display_name) if ov else m.display_name
        description = (ov.description or m.capabilities) if ov else m.capabilities
        priority = ov.priority if ov else 0
        result.append(
            {
                "key": m.key,
                "provider": m.provider,
                "displayName": display,
                "modelId": m.model_id,
                "available": bool(m.api_key) and enabled,
                "enabled": enabled,
                "freeTier": free_tier,
                "capabilities": description,
                "isDefault": m.key == default_key,
                "priority": priority,
            }
        )
    result.sort(key=lambda x: (x["priority"], x["key"]))
    return result


def get_llm_provider(model_key: str | None = None) -> LLMProvider:
    """Build the provider for ``model_key`` (default when omitted/unknown).

    Backward compatible with the old ``get_llm_provider()``: unknown keys fall
    back to the default (the API layer validates keys first and returns a 4xx),
    and a model without a configured key returns the mock — exactly the old
    demo-mode behaviour.
    """
    try:
        spec = get_model(model_key)
    except ValueError:
        spec = get_model(None)
    if not spec.api_key or not is_enabled(spec.key):
        return MockLLMProvider()
    return OpenAICompatibleLLMProvider(
        api_key=spec.api_key,
        base_url=spec.base_url,
        model=spec.model_id,
        name=f"{spec.key}",
    )
