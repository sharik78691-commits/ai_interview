"""Tests for interviewer audio capture (tab/meeting) -> STT -> existing AI flow.

External STT is always mocked: no network calls in tests.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.models.ai_response import AIInterviewResponse
from app.providers.stt.provider import (
    GroqWhisperSTTProvider,
    MockSTTProvider,
    STTRequestError,
    STTUnavailableError,
    get_stt_provider,
)


def _fake_guidance() -> AIInterviewResponse:
    return AIInterviewResponse(
        question="Can you explain the Saga pattern?",
        questionType="technical",
        answerPoints=["Define Saga", "Explain compensation"],
        followUpQuestions=["Choreography vs orchestration?"],
        responseLength="medium",
    )


def _mocked_ai(ai_cls) -> None:
    ai_cls.return_value.analyze = AsyncMock(return_value=_fake_guidance())
    ai_cls.return_value.last_warning = None


class TestConfigLoading:
    """backend/.env must load regardless of the server's working directory.

    Regression guard: python-dotenv searches from CWD upwards, so running
    uvicorn from the project root silently produced an unconfigured provider,
    which surfaced as "Browser audio capture is unavailable".
    """

    def test_env_file_path_is_absolute_and_under_backend(self) -> None:
        from app.core import config

        assert config._BACKEND_DIR.name == "backend"
        assert (config._BACKEND_DIR / ".env").is_file()

    def test_stt_settings_come_from_env_file(self) -> None:
        from app.core.config import get_settings

        settings = get_settings()
        # Values must come from backend/.env, not from the CWD lookup.
        assert settings.stt_model.endswith("whisper-1") or "whisper" in settings.stt_model


class TestSTTProviders:
    def test_mock_provider_is_not_available(self) -> None:
        p = MockSTTProvider()
        assert p.available is False

    async def test_mock_provider_returns_empty_text(self) -> None:
        assert await MockSTTProvider().transcribe(b"12345") == ""

    def test_provider_uses_stt_key_when_present(self) -> None:
        p = GroqWhisperSTTProvider(api_key="sk-test", base_url="https://x/v1", model="m")
        assert p.available is True
        assert p.model == "m"

    def test_provider_falls_back_to_llm_key(self) -> None:
        # No explicit STT key -> reuse the LLM key (Groq serves Whisper there).
        with patch("app.providers.stt.provider.get_settings") as gs:
            s = gs.return_value
            s.stt_api_key = ""
            s.llm_api_key = "llm-key"
            s.stt_base_url = ""
            s.llm_base_url = "https://groq/v1"
            s.stt_model = "whisper-large-v3-turbo"
            p = GroqWhisperSTTProvider()
        assert p.available is True
        assert p.api_key == "llm-key"
        assert p.base_url == "https://groq/v1"

    async def test_transcribe_without_key_raises(self) -> None:
        p = GroqWhisperSTTProvider(api_key="", base_url="https://x/v1", model="m")
        p.api_key = ""  # simulate unconfigured
        with pytest.raises(STTUnavailableError):
            await p.transcribe(b"data")

    async def test_transcribe_posts_to_whisper_endpoint(self) -> None:
        """Real provider must hit /audio/transcriptions and read .text."""
        p = GroqWhisperSTTProvider(api_key="sk-test", base_url="https://x/v1", model="whisper")
        response = type(
            "R",
            (),
            {
                "status_code": 200,
                "json": lambda self: {"text": "Can you explain the Saga pattern?"},
                "text": "",
            },
        )()

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, **kwargs):
                assert url == "https://x/v1/audio/transcriptions"
                return response

        with patch("httpx.AsyncClient", return_value=FakeClient()):
            text = await p.transcribe(b"fake-audio", mime_type="audio/webm")
        assert text == "Can you explain the Saga pattern?"

    def test_factory_returns_working_provider_with_key(self) -> None:
        with patch("app.providers.stt.provider.get_settings") as gs:
            s = gs.return_value
            s.stt_api_key = "stt-key"
            s.llm_api_key = ""
            s.stt_base_url = "https://x/v1"
            s.llm_base_url = "https://x/v1"
            s.stt_model = "whisper"
            provider = get_stt_provider()
        assert isinstance(provider, GroqWhisperSTTProvider)


def _response(status: int, text: str = "", headers: dict | None = None):
    """Minimal stand-in for httpx.Response."""
    return type(
        "R",
        (),
        {
            "status_code": status,
            "text": text,
            "headers": headers or {},
            "json": lambda self: {"text": "Can you explain the Saga pattern?"},
        },
    )()


class _ClientFactory:
    """Fake httpx.AsyncClient that returns a scripted list of responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, *a, **kw):
        factory = self

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, **kwargs):
                factory.calls += 1
                return factory.responses.pop(0)

        return FakeClient()


class TestAudioSniffing:
    """The backend must label a clip with what its bytes actually are."""

    def test_detects_wav(self) -> None:
        from app.api.websocket import MIME_BY_FORMAT, sniff_audio

        wav = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 40
        assert sniff_audio(wav) == "wav"
        assert MIME_BY_FORMAT["wav"] == "audio/wav"

    def test_detects_webm_and_other_containers(self) -> None:
        from app.api.websocket import sniff_audio

        assert sniff_audio(b"\x1a\x45\xdf\xa3" + b"\x00" * 32) == "webm"
        assert sniff_audio(b"fLaC" + b"\x00" * 32) == "flac"
        assert sniff_audio(b"    ftypisom" + b"\x00" * 32) == "mp4"
        assert sniff_audio(b"OggS" + b"\x00" * 32) == "ogg"
        assert sniff_audio(b"ID3\x04" + b"\x00" * 32) == "mp3"

    def test_rejects_garbage(self) -> None:
        from app.api.websocket import sniff_audio

        assert sniff_audio(b"") == "unknown"
        assert sniff_audio(b"just some text") == "unknown"
        assert sniff_audio(b"\x00" * 6) == "unknown"

    @patch("app.api.websocket.get_stt_provider")
    @patch("app.api.websocket.AIService")
    def test_backend_corrects_a_wrong_declared_mime(self, ai_cls, stt_factory) -> None:
        """WAV bytes declared as audio/webm must be re-labelled before upload."""
        from fastapi.testclient import TestClient

        from app.main import app

        wav = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 64
        transcribed = AsyncMock(return_value="What is your biggest failure?")
        stt_factory.return_value = type(
            "FakeSTT", (), {"transcribe": transcribed}
        )()
        _mocked_ai(ai_cls)

        with TestClient(app) as client:
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()
                # Client claims webm, bytes are actually WAV.
                ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/webm"})
                ws.send_bytes(wav)
                ws.send_json({"type": "audio_flush", "mimeType": "audio/webm"})
                for _ in range(5):
                    msg = ws.receive_json()
                    if msg.get("type") == "stt_transcript":
                        break

        assert transcribed.await_count == 1
        assert transcribed.call_args.kwargs["mime_type"] == "audio/wav"

    @patch("app.api.websocket.get_stt_provider")
    @patch("app.api.websocket.AIService")
    def test_unreadable_clip_is_reported_not_uploaded(self, ai_cls, stt_factory) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        transcribed = AsyncMock(return_value="should never be called")
        stt_factory.return_value = type(
            "FakeSTT", (), {"transcribe": transcribed}
        )()
        _mocked_ai(ai_cls)

        with TestClient(app) as client:
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()
                ws.send_json({"type": "interviewer_audio_start"})
                ws.send_bytes(b"not-audio-at-all")
                ws.send_json({"type": "audio_flush"})
                messages = []
                for _ in range(4):
                    msg = ws.receive_json()
                    messages.append(msg)
                    if msg.get("type") == "error":
                        break

        assert transcribed.await_count == 0, "undecodable bytes must not reach the API"
        errors = [m for m in messages if m.get("type") == "error"]
        assert errors
        assert "paste the question manually" in errors[0]["message"]

    def test_filename_follows_declared_mime(self) -> None:
        from app.providers.stt.provider import GroqWhisperSTTProvider

        assert GroqWhisperSTTProvider.filename_for("audio/wav") == "clip.wav"
        assert GroqWhisperSTTProvider.filename_for("audio/webm") == "clip.webm"
        assert GroqWhisperSTTProvider.filename_for("audio/ogg") == "clip.ogg"
        assert GroqWhisperSTTProvider.filename_for("") == "clip.wav"


class TestSTTReliability:
    """Rate limits and transient errors must not kill the interview."""

    async def test_retries_rate_limit_then_succeeds(self) -> None:
        p = GroqWhisperSTTProvider(api_key="k", base_url="https://x/v1", model="whisper")
        client = _ClientFactory(
            [
                _response(429, "rate limited", {"Retry-After": "0"}),
                _response(200),
            ]
        )
        with patch("httpx.AsyncClient", new=client):
            text = await p.transcribe(b"wav-bytes")
        assert text == "Can you explain the Saga pattern?"
        assert client.calls == 2  # retried once, then succeeded

    async def test_honours_retry_after_header(self) -> None:
        p = GroqWhisperSTTProvider(api_key="k", base_url="https://x/v1", model="whisper")
        client = _ClientFactory(
            [_response(429, "slow down", {"Retry-After": "7"})] * 3
        )
        with patch("httpx.AsyncClient", new=client), patch(
            "app.providers.stt.provider.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            with pytest.raises(STTRequestError):
                await p.transcribe(b"wav")
        assert sleep.await_args_list[0].args[0] == 7.0

    async def test_retries_server_error_then_succeeds(self) -> None:
        p = GroqWhisperSTTProvider(api_key="k", base_url="https://x/v1", model="whisper")
        client = _ClientFactory([_response(503, "overloaded"), _response(200)])
        with patch("httpx.AsyncClient", new=client), patch(
            "app.providers.stt.provider.asyncio.sleep", new=AsyncMock()
        ):
            assert await p.transcribe(b"wav") == "Can you explain the Saga pattern?"
        assert client.calls == 2

    async def test_gives_up_after_max_attempts(self) -> None:
        p = GroqWhisperSTTProvider(api_key="k", base_url="https://x/v1", model="whisper")
        client = _ClientFactory([_response(429, "rate limited"), _response(429), _response(429)])
        with patch("httpx.AsyncClient", new=client), patch(
            "app.providers.stt.provider.asyncio.sleep", new=AsyncMock()
        ):
            with pytest.raises(STTRequestError):
                await p.transcribe(b"wav")
        assert client.calls == 3

    async def test_bad_media_is_not_retried(self) -> None:
        """HTTP 400 is permanent - retrying only wastes quota."""
        p = GroqWhisperSTTProvider(api_key="k", base_url="https://x/v1", model="whisper")
        client = _ClientFactory(
            [_response(400, '{"error":{"message":"could not process file - is it a valid media file?"}}')]
        )
        with patch("httpx.AsyncClient", new=client):
            with pytest.raises(STTRequestError) as exc:
                await p.transcribe(b"bad-bytes")
        assert client.calls == 1
        # The message must tell the user how to fix it.
        assert "Also share tab audio" in str(exc.value)

    def test_describe_bad_model_points_at_env_var(self) -> None:
        msg = GroqWhisperSTTProvider._describe(
            404, '{"error":{"message":"The model `whisper-1` does not exist"}}'
        )
        assert "STT_MODEL" in msg
        assert "whisper-large-v3-turbo" in msg


class TestInterviewerQuestionFlow:
    """The interviewer path must reuse the SAME question detection + AI service."""

    def test_interviewer_text_goes_through_question_buffer(self) -> None:
        from app.services.question_service import QuestionBuffer

        buf = QuestionBuffer()
        # Clips are cut at a pause, so a finished utterance fires immediately.
        assert buf.push_complete("Can you explain the Saga pattern?") == (
            "Can you explain the Saga pattern?"
        )
        # Same sentence spoken without the question mark still counts.
        assert buf.push_complete("Can you explain the Saga pattern") == (
            "Can you explain the Saga pattern"
        )
        # Imperative prompts (no "?", no question word) MUST also fire the AI.
        # This is the real-world case: "give me a brief about Spring..." etc.
        assert buf.push_complete(
            "Give me a brief about the Spring API and the architecture of your project"
        ) == "Give me a brief about the Spring API and the architecture of your project"
        assert buf.push_complete(
            "Brief about the string API entirely connected and about the architecture"
        ) == "Brief about the string API entirely connected and about the architecture"
        # Long multi-sentence prompts fire as-is (speed comes from bounded
        # output tokens, not from gating the input).
        long_prompt = " ".join(["Explain event delegation in JavaScript."] * 20)
        assert buf.push_complete(long_prompt) == long_prompt
        # Backchannels are too short to answer — these stay silent.
        assert buf.push_complete("") is None
        assert buf.push_complete("why") is None
        assert buf.push_complete("Okay nice") is None
        assert buf.push_complete("Thank you") is None

    def test_mic_buffer_still_joins_fragments(self) -> None:
        """The microphone path keeps its original incremental behaviour."""
        from app.services.question_service import QuestionBuffer

        buf = QuestionBuffer()
        assert buf.push("Can you explain") is None
        result = buf.push("event delegation in JavaScript?")
        assert result is not None
        assert "event delegation" in result

    @patch("app.api.websocket.get_stt_provider")
    @patch("app.api.websocket.AIService")
    def test_ws_transcribes_then_generates_answer(self, ai_cls, stt_factory) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        # STT returns one complete interviewer question.
        stt_factory.return_value = type(
            "FakeSTT",
            (),
            {"transcribe": AsyncMock(return_value="Can you explain the Saga pattern?")},
        )()
        ai_cls.return_value.analyze = AsyncMock(return_value=_fake_guidance())
        ai_cls.return_value.last_warning = None

        with patch("app.main.app"), TestClient(app) as client:
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()  # handshake status
                ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/webm"})
                # Binary frame = one audio clip.
                ws.send_bytes(b"\x1a\x45\xdf\xa3fake-webm-bytes")
                ws.send_json({"type": "audio_flush"})

                seen = {}
                for _ in range(6):
                    msg = ws.receive_json()
                    if msg.get("type") == "stt_transcript":
                        seen["stt"] = msg["text"]
                    if msg.get("type") == "ai_guidance":
                        seen["guidance"] = msg["data"]
                        break
                    if msg.get("type") == "error":
                        seen["error"] = msg["message"]
                        break

        assert seen.get("stt") == "Can you explain the Saga pattern?"
        assert isinstance(seen.get("guidance"), dict), seen
        assert seen["guidance"]["answerPoints"], seen
        # Same AIService.analyze used by the microphone path.
        ai_cls.return_value.analyze.assert_awaited()

    @patch("app.api.websocket.get_stt_provider")
    @patch("app.api.websocket.AIService")
    def test_ws_manual_question_uses_existing_ai_flow(self, ai_cls, stt_factory) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        stt_factory.return_value = MockSTTProvider()
        _mocked_ai(ai_cls)

        with patch("app.main.app"), TestClient(app) as client:
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()
                ws.send_json(
                    {
                        "type": "interviewer_question",
                        "text": "Tell me about a production incident.",
                        "responseLength": "long",
                    }
                )
                texts = []
                guidance = False
                for _ in range(6):
                    msg = ws.receive_json()
                    if msg.get("type") == "stt_transcript":
                        texts.append(msg["text"])
                    if msg.get("type") == "ai_guidance":
                        guidance = True
                        break

        assert texts == ["Tell me about a production incident."]
        assert guidance is True
        assert ai_cls.return_value.response_length == "long"

    @patch("app.api.websocket.get_stt_provider")
    @patch("app.api.websocket.AIService")
    def test_ws_reports_manual_fallback_when_stt_unavailable(self, ai_cls, stt_factory) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        stt_factory.return_value = type(
            "FailSTT",
            (),
            {"transcribe": AsyncMock(side_effect=STTUnavailableError("no key"))},
        )()
        ai_cls.return_value.analyze = AsyncMock(return_value=_fake_guidance())
        ai_cls.return_value.last_warning = None

        with patch("app.main.app"), TestClient(app) as client:
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()
                ws.send_json({"type": "interviewer_audio_start"})
                ws.send_bytes(b"audio")
                ws.send_json({"type": "audio_flush"})
                messages = []
                for _ in range(4):
                    msg = ws.receive_json()
                    messages.append(msg)
                    if msg.get("type") == "error":
                        break

        errors = [m for m in messages if m.get("type") == "error"]
        assert errors, messages
        assert "paste the question manually" in errors[0]["message"]