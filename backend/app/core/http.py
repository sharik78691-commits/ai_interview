"""Shared HTTP client (connection reuse across requests).

Creating a fresh ``httpx.AsyncClient`` per call renegotiates TLS every time —
for a realtime assistant that means ~50–150ms of avoidable latency on every
AI and speech-to-text request, and more under a burst of questions.

A single client per running event loop keeps connections warm (keep-alive +
connection pooling). The client is recreated only when the running loop
changes (pytest-asyncio spins a fresh loop per test; uvicorn keeps one loop
for the whole process). The old client is simply dropped for GC — never
awaited, because a dead loop can't run ``aclose()``.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx

# Generous default: connect/write/pool 15s, read 60s (streaming + STT need the
# long read). Callers tighten it per-request via `timeout=` where relevant.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0)
# keepalive_expiry: httpx drops an idle pooled connection after 5s by default.
# In a live interview the gap between two questions is almost always longer,
# so EVERY clip paid a fresh TCP+TLS setup — measured 2026-10-20 on DeepInfra
# STT with an 8s gap: 3.33s median at 5s expiry vs 0.65s at 120s.
KEEPALIVE_EXPIRY_SECONDS = 120.0
_LIMITS = httpx.Limits(
    max_connections=50,
    max_keepalive_connections=20,
    keepalive_expiry=KEEPALIVE_EXPIRY_SECONDS,
)

_current_loop: asyncio.AbstractEventLoop | None = None
_client: httpx.AsyncClient | None = None


def shared_client() -> httpx.AsyncClient:
    """Return a warm, reusable client bound to the running event loop."""
    global _current_loop, _client
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (sync context): a throwaway client is fine.
        return httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, limits=_LIMITS)
    if _client is None or _current_loop is not loop or _client.is_closed:
        _client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, limits=_LIMITS)
        _current_loop = loop
    return _client


@asynccontextmanager
async def shared_http():
    """``async with`` form that yields the shared client WITHOUT closing it.

    Lets call sites keep their existing ``async with client:`` shape while
    sharing one pooled client across every request.
    """
    yield shared_client()


async def prewarm(url: str, timeout: float = 5.0) -> bool:
    """Open (and pool) a connection to ``url``'s host ahead of the first real call.

    The response itself is irrelevant (401/404 are fine) — what matters is that
    the TCP+TLS handshake is already done when the first question arrives.
    Never raises: a failed warm-up just means the first call pays the setup.
    """
    try:
        await shared_client().get(url, timeout=timeout)
        return True
    except Exception:
        return False
