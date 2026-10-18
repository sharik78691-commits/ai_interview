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
    # Which request field carries the output token cap. OpenAI's newer
    # reasoning models (GPT-6 Luna) reject ``max_tokens`` and require
    # ``max_completion_tokens``; every other provider uses ``max_tokens``.
    max_tokens_param: str = "max_tokens"
    # Reasoning models accept only temperature=1 (default) — set False to omit
    # the temperature field entirely for them.
    supports_temperature: bool = True
    # Optional `reasoning_effort` ("low"|"medium"|"high") sent with the request.
    # Asking for a shorter think is the biggest measured lever on time-to-
    # first-bullet for reasoning models (openai/gpt-oss-120b: 1.12s -> 0.50s to
    # the first visible bullet, total 1.56s -> 0.84s, JSON valid either way).
    # None means "leave the provider's default alone" — it is deliberately NOT
    # set for models where it was measured neutral or negative (Gemini flash-lite
    # 0.71s vs 0.74s) or where the field is rejected outright (Gemini flash-lite
    # rejects "none" with HTTP 400). A rejected value is dropped and retried
    # once by the provider, so this can degrade safely.
    reasoning_effort: str | None = None


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
            # gpt-oss is a REASONING model: at the default effort it spends a
            # silent 0.6s thinking before the first bullet appears, and at a
            # 700-token cap it could burn the whole budget on reasoning and
            # return truncated JSON. Verified 200 on this endpoint (it enforces
            # the low|medium|high enum) and measured: first bullet 1.12s -> 0.50s,
            # total 1.56s -> 0.84s, valid JSON in both cases. Also cuts TPM
            # consumption, which is this account's tightest free-tier limit
            # (8000 tokens/min).
            reasoning_effort="low",
        ),
        ModelSpec(
            key="gemini-flash",
            provider="Google",
            display_name="Gemini 3.5 Flash-Lite — Google",
            model_id=s.gemini_model,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            api_key=s.gemini_api_key,
            free_tier=True,
            capabilities="Google's fastest, low-cost Gemini 3.5 Flash-Lite model",
        ),
        ModelSpec(
            key="gemini-3.5-flash",
            provider="Google",
            display_name="Gemini 3.5 Flash — Google",
            model_id=s.gemini_35_model,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            api_key=s.gemini_api_key,
            free_tier=True,
            capabilities="Google's Gemini 3.5 Flash (full Flash — replaces the retired 2.5 Flash)",
            # Full Flash is the SLOWEST model in the catalog (measured 4.43s to
            # the first visible bullet vs 0.71s for Flash-Lite) because it
            # thinks first. `low` is validated by this endpoint (it rejects an
            # out-of-enum value with 400, so the field is honoured, not ignored)
            # and measured 4.43s -> 3.38s to first bullet. The two Gemini models
            # where it measured neutral or negative are deliberately left unset.
            reasoning_effort="low",
        ),
        ModelSpec(
            key="gemini-3.1-flash-lite",
            provider="Google",
            display_name="Gemini 3.1 Flash-Lite — Google",
            model_id=s.gemini_31_lite_model,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            api_key=s.gemini_api_key,
            free_tier=True,
            capabilities="Google's Gemini 3.1 Flash-Lite (lightweight, low-cost)",
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
        ModelSpec(
            key="openai-gpt6-luna",
            provider="OpenAI",
            display_name="GPT-6 Luna — OpenAI",
            model_id=s.openai_model,
            base_url="https://api.openai.com/v1",
            api_key=s.openai_api_key,
            free_tier=False,
            capabilities="OpenAI's cost-efficient GPT-6 Luna — fast, reliable answers",
            max_tokens_param="max_completion_tokens",
            supports_temperature=False,
        ),
        ModelSpec(
            key="deepseek-flash",
            provider="DeepSeek",
            display_name="DeepSeek-V4.1 Flash — DeepSeek",
            model_id=s.deepseek_model,
            base_url="https://api.deepseek.com/v1",
            api_key=s.deepseek_api_key,
            free_tier=False,
            capabilities="DeepSeek's speed tier — quick, low-latency answers for live interviews",
            # Reasoning model whose effort DEFAULTS to "high" per DeepSeek's
            # own GET /models ("effort": supported_levels [low, high, max],
            # default_level high). For the Flash tier the whole point is speed,
            # so ask for a short think: measured elsewhere this is the lever
            # that moves time-to-first-bullet (Groq gpt-oss 1.12s -> 0.50s).
            # The field name + value are VERIFIED against DeepSeek: an invalid
            # value returns HTTP 422 listing `none, minimal, low, medium, high,
            # xhigh, ultra, max`, and `reasoning_effort=low` clears validation
            # (it reaches billing, which returns 402 on an empty balance).
            # Couldn't be measured end-to-end yet — the account has no balance.
            reasoning_effort="low",
        ),
        ModelSpec(
            key="deepseek-v4-pro",
            provider="DeepSeek",
            display_name="DeepSeek-V4 Pro — DeepSeek",
            model_id=s.deepseek_v4_pro_model,
            base_url="https://api.deepseek.com/v1",
            api_key=s.deepseek_api_key,
            free_tier=False,
            capabilities="DeepSeek's capability tier — deeper, more thorough answers",
            # Deliberately NO reasoning_effort: this is the capability tier, so
            # it keeps DeepSeek's default (effort=high) rather than trading
            # depth for speed. Flip to "low" (one line) if live measurement
            # shows the default effort makes the first bullet arrive too late.
            reasoning_effort=None,
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
    llm_keys = {m.key for m in _catalog()}
    overrides = _load_overrides()
    for key, ov in overrides.items():
        # Ignore non-LLM rows (e.g. STT models sharing ai_model_config).
        if key in llm_keys and ov.is_default:
            return key
    env_default = os.getenv("DEFAULT_LLM_MODEL", "").strip()
    if env_default in llm_keys:
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
        max_tokens_param=spec.max_tokens_param,
        supports_temperature=spec.supports_temperature,
        reasoning_effort=spec.reasoning_effort,
    )
