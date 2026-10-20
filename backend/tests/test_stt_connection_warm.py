"""STT speed: pooled connections stay warm between interview questions."""
import asyncio
from unittest.mock import patch

from app.core import http as http_mod
from app.providers.stt import provider as stt


def test_pooled_connections_survive_the_gap_between_questions():
    """Regression: httpx's 5s default made every clip pay a fresh TLS setup."""
    assert http_mod._LIMITS.keepalive_expiry >= 60
    assert http_mod._LIMITS.keepalive_expiry == http_mod.KEEPALIVE_EXPIRY_SECONDS


def test_prewarm_never_raises():
    class Boom:
        async def get(self, *a, **k):
            raise RuntimeError("network down")

    with patch.object(http_mod, "shared_client", return_value=Boom()):
        assert asyncio.run(http_mod.prewarm("https://example.invalid")) is False


def test_prewarm_stt_warms_the_active_http_provider():
    warmed = []

    async def fake_prewarm(url, timeout=5.0):
        warmed.append(url)
        return True

    p = stt.GroqWhisperSTTProvider(api_key="k", base_url=stt.DEEPINFRA_OPENAI_URL, model="m")
    with patch.object(stt, "get_stt_provider", return_value=p), patch.object(stt, "prewarm", fake_prewarm):
        assert asyncio.run(stt.prewarm_stt()) is True
    assert warmed == [stt.DEEPINFRA_OPENAI_URL]


def test_prewarm_stt_skips_mock_and_websocket_providers():
    async def fail(*a, **k):
        raise AssertionError("must not warm")

    with patch.object(stt, "get_stt_provider", return_value=stt.MockSTTProvider()), patch.object(stt, "prewarm", fail):
        assert asyncio.run(stt.prewarm_stt()) is False
    live = stt.GeminiLiveSTTProvider(api_key="g", model="m")
    with patch.object(stt, "get_stt_provider", return_value=live), patch.object(stt, "prewarm", fail):
        assert asyncio.run(stt.prewarm_stt()) is False
