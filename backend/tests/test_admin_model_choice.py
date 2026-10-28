"""The admin-selected STT and LLM are the ONLY ones used.

Bug (2026-10-28): with Whisper (DeepInfra) as the admin STT and GPT-OSS 120B
(Cerebras) as the admin LLM, two hidden paths still used Google Gemini:
* the word-by-word live transcript opened a Gemini Live session whenever a
  Gemini key existed — so when Gemini refused (billing hold) the transcript
  stopped, although the admin never chose Gemini;
* a rate-limited / failing LLM switched silently to Gemini Flash (then Groq).

Pinned:
1. the live stream is used only when the admin STT is Gemini Transcribe Live;
   with any other STT the handshake asks for snapshot previews, which go
   through that admin STT;
2. a failing admin LLM never hands the question to another model.
"""
from unittest.mock import AsyncMock, patch

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.ai_model_config import AIModelConfig
from app.providers.llm.provider import LLMQuotaError, MockLLMProvider
from app.providers.stt import provider as stt_provider
from app.services.ai_service import AIService
from tests.test_stt_live_stream import _auth


def _admin_stt(key: str) -> None:
    with SessionLocal() as db:
        db.add(AIModelConfig(key=key, enabled=True, is_default=True))
        db.commit()


class TestLiveTranscriptFollowsAdminSTT:
    def test_deepinfra_whisper_selected_means_no_gemini_stream(self) -> None:
        s = get_settings()
        with patch.object(s, "gemini_api_key", "gemini-key"), patch.object(s, "deepinfra_api_key", "di-key"):
            _admin_stt("whisper-deepinfra")
            assert stt_provider.live_stream_selected() is False
            assert stt_provider.get_stt_provider().model == s.stt_model_5  # Whisper on DeepInfra

    def test_gemini_live_selected_uses_the_stream(self) -> None:
        with patch.object(get_settings(), "gemini_api_key", "gemini-key"):
            _admin_stt("gemini-3.5-transcribe-live")
            assert stt_provider.live_stream_selected() is True

    def test_handshake_asks_for_snapshot_previews_with_deepinfra(self) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        s = get_settings()
        connect = AsyncMock(side_effect=AssertionError("Gemini Live must not be contacted"))
        with patch.object(s, "gemini_api_key", "gemini-key"), patch.object(s, "deepinfra_api_key", "di-key"), \
                patch("app.providers.stt.live_stream._connect", connect):
            with patch("app.main.app"), TestClient(app) as client:
                _auth(client)
                _admin_stt("whisper-deepinfra")
                with client.websocket_connect("/ws/interview") as ws:
                    hello = ws.receive_json()
        assert (hello["liveStream"], hello["liveAnswers"], hello["livePreview"]) == (False, False, True)
        connect.assert_not_called()


class _Failing(MockLLMProvider):
    name = "gpt-oss-cerebras"

    async def analyze_question_stream(self, *a, **k):
        raise LLMQuotaError("Rate limited")

    async def analyze_question(self, *a, **k):
        raise LLMQuotaError("Rate limited")


async def test_failing_admin_llm_never_switches_to_another_model() -> None:
    other = AsyncMock(side_effect=AssertionError("no other model may be asked"))
    with patch("app.services.ai_service.get_llm_provider", lambda key=None: _Failing()), \
            patch("app.providers.llm.registry.get_llm_provider", other):
        svc = AIService()
        result = await svc.analyze("What is REST?", on_delta=AsyncMock())
    assert result is not None  # template guidance keeps the interview going
    assert "Rate limited" in (svc.last_warning or "")
    assert svc.last_fallback is None
    other.assert_not_called()
