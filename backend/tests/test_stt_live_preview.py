"""Live interviewer transcript (audio_preview) regressions — mocked providers.

Before: interviewer text appeared in the Transcript panel only AFTER the whole
sentence (clip cut on ~0.85s of silence, then STT). Now the client sends rolling
snapshots of the clip still being spoken and the backend answers with
PROVISIONAL `stt_transcript` frames (`final: false`).

Invariants pinned here:
1. a preview is shown (final: false, seq of the open clip) — and NEVER answered;
2. a preview that finishes after its clip's final is dropped as stale;
3. STT_LIVE_PREVIEW=false disables the lane and the handshake says so;
4. a failing preview is silent (no error/notice) and pauses the lane;
5. previews make ONE provider attempt; finals keep the full retry budget.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.models.ai_response import AIInterviewResponse

WEBM = b"\x1a\x45\xdf\xa3"


def _auth(client) -> None:
    client.post(
        "/api/auth/register",
        json={"email": "ws-preview@example.com", "password": "Passw0rd123", "name": "WS"},
    )


def _fake_stt(side_effect):
    # AsyncMock (not a plain async def) so `self` is never bound to the clip.
    return type("FakeSTT", (), {"transcribe": AsyncMock(side_effect=side_effect)})()


def _recv_until(ws, pred, limit: int = 30) -> list[dict]:
    """Read frames until ``pred(frame)``; pings keep a lost frame from hanging."""
    frames: list[dict] = []
    for i in range(limit):
        if i % 3 == 2:
            ws.send_json({"type": "ping"})
        msg = ws.receive_json()
        frames.append(msg)
        if pred(msg):
            return frames
    raise AssertionError(f"condition never met: {frames}")


def _is_pong(m: dict) -> bool:
    return m.get("type") == "pong"


class TestLivePreview:
    @patch("app.api.websocket.get_stt_provider")
    @patch("app.api.websocket.AIService")
    def test_preview_is_shown_but_never_answered(self, ai_cls, stt_factory) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        stt_factory.return_value = _fake_stt(lambda clip, mime_type=None: "Can you explain the Saga")
        ai_cls.return_value.analyze = AsyncMock()
        ai_cls.return_value.last_warning = None

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                hello = ws.receive_json()
                assert hello.get("livePreview") is True
                ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/webm"})
                ws.receive_json()  # "Listening for the interviewer's question"
                ws.send_bytes(WEBM + b"snapshot")
                ws.send_json({"type": "audio_preview", "mimeType": "audio/webm"})
                frames = _recv_until(
                    ws, lambda m: m.get("type") == "stt_transcript"
                )
                # Give a (wrongly) triggered answer time to show up.
                ws.send_json({"type": "ping"})
                frames += _recv_until(ws, _is_pong)

        preview = frames[[m.get("type") for m in frames].index("stt_transcript")]
        assert preview == {
            "type": "stt_transcript",
            "text": "Can you explain the Saga",
            "final": False,
            "seq": 0,
        }
        assert not any(m.get("type") == "ai_guidance" for m in frames), frames
        assert not any("Question detected" in str(m.get("message", "")) for m in frames)
        ai_cls.return_value.analyze.assert_not_awaited()

    @patch("app.api.websocket.get_stt_provider")
    @patch("app.api.websocket.AIService")
    def test_preview_finishing_after_its_final_is_dropped(self, ai_cls, stt_factory) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        async def _transcribe(clip, mime_type=None):
            if clip.endswith(b"snapshot"):
                await asyncio.sleep(0.6)  # still at the provider when the clip ends
                return "Tell me about"
            return "Tell me about yourself."

        stt_factory.return_value = _fake_stt(_transcribe)
        ai_cls.return_value.analyze = AsyncMock(
            return_value=AIInterviewResponse(
                question="Tell me about yourself.",
                questionType="behavioral",
                answerPoints=["Intro"],
                followUpQuestions=[],
                responseLength="medium",
            )
        )
        ai_cls.return_value.last_warning = None

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()  # handshake
                ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/webm"})
                ws.send_bytes(WEBM + b"snapshot")
                ws.send_json({"type": "audio_preview"})
                ws.send_bytes(WEBM + b"final-clip")
                ws.send_json({"type": "audio_flush"})
                frames = _recv_until(ws, lambda m: m.get("type") == "ai_guidance")
                # Outlive the slow preview, then make sure it never surfaced.
                ws.send_json({"type": "ping"})
                frames += _recv_until(ws, _is_pong)
                import time

                time.sleep(0.7)
                ws.send_json({"type": "ping"})
                frames += _recv_until(ws, _is_pong)

        transcripts = [m for m in frames if m.get("type") == "stt_transcript"]
        assert transcripts == [
            {"type": "stt_transcript", "text": "Tell me about yourself.", "final": True, "seq": 0}
        ], transcripts
        # The snapshot was NOT glued onto the final clip.
        final_clip = stt_factory.return_value.transcribe.await_args_list[-1].args[0]
        assert final_clip == WEBM + b"final-clip"
        assert ai_cls.return_value.analyze.await_count == 1

    @patch("app.api.websocket.get_stt_provider")
    def test_disabled_flag_skips_previews(self, stt_factory) -> None:
        from fastapi.testclient import TestClient

        from app.core.config import get_settings
        from app.main import app

        stt_factory.return_value = _fake_stt(lambda clip, mime_type=None: "should not run")

        with patch.object(get_settings(), "stt_live_preview", False):
            with patch("app.main.app"), TestClient(app) as client:
                _auth(client)
                with client.websocket_connect("/ws/interview") as ws:
                    hello = ws.receive_json()
                    ws.send_json({"type": "interviewer_audio_start"})
                    ws.send_bytes(WEBM + b"snapshot")
                    ws.send_json({"type": "audio_preview"})
                    ws.send_json({"type": "ping"})
                    frames = _recv_until(ws, _is_pong)

        assert hello.get("livePreview") is False
        assert not any(m.get("type") == "stt_transcript" for m in frames), frames
        stt_factory.return_value.transcribe.assert_not_awaited()

    @patch("app.api.websocket.get_stt_provider")
    def test_failing_preview_is_silent_and_pauses_the_lane(self, stt_factory) -> None:
        from fastapi.testclient import TestClient

        from app.main import app
        from app.providers.stt.provider import STTRequestError

        stt_factory.return_value = _fake_stt(STTRequestError("rate limit"))

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()  # handshake
                ws.send_json({"type": "interviewer_audio_start"})
                ws.send_bytes(WEBM + b"clip-one")
                ws.send_json({"type": "audio_preview"})
                ws.send_json({"type": "ping"})
                frames = _recv_until(ws, _is_pong)
                ws.send_bytes(WEBM + b"clip-two")
                ws.send_json({"type": "audio_preview"})
                ws.send_json({"type": "ping"})
                frames += _recv_until(ws, _is_pong)

        assert not any(m.get("type") in ("error", "notice") for m in frames), frames
        # The second snapshot fell inside the cooldown: no second provider call.
        assert stt_factory.return_value.transcribe.await_count == 1

    @patch("app.api.websocket.get_stt_provider")
    def test_one_failed_preview_does_not_freeze_the_transcript(self, stt_factory) -> None:
        """Before: one failed/slow snapshot paused previews for 20 s (the
        final-clip cooldown), so the Transcript line froze mid-question."""
        import time

        from fastapi.testclient import TestClient

        from app.main import app
        from app.providers.stt.provider import STTRequestError

        stt_factory.return_value = _fake_stt([STTRequestError("timeout"), "Can you explain"])

        with patch("app.api.websocket.PREVIEW_RETRY_SECONDS", 0.05), patch(
            "app.api.websocket.PREVIEW_COOLDOWN_SECONDS", 999.0
        ), patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()  # handshake
                ws.send_json({"type": "interviewer_audio_start"})
                ws.send_bytes(WEBM + b"clip-one")
                ws.send_json({"type": "audio_preview"})
                ws.send_json({"type": "ping"})
                frames = _recv_until(ws, _is_pong)
                time.sleep(0.1)  # past the short preview retry, far inside 20 s
                ws.send_bytes(WEBM + b"clip-one-longer")
                ws.send_json({"type": "audio_preview"})
                frames += _recv_until(ws, lambda m: m.get("type") == "stt_transcript")

        live = [m for m in frames if m.get("type") == "stt_transcript"]
        assert live and live[-1]["text"] == "Can you explain" and live[-1]["final"] is False
        assert stt_factory.return_value.transcribe.await_count == 2

    @patch("app.api.websocket.get_stt_provider")
    def test_preview_provider_gets_one_attempt_final_keeps_retries(self, stt_factory) -> None:
        from fastapi.testclient import TestClient

        from app.main import app
        from app.providers.stt.provider import MAX_ATTEMPTS, MockSTTProvider

        made: list[MockSTTProvider] = []

        def _factory():
            made.append(MockSTTProvider())
            return made[-1]

        stt_factory.side_effect = _factory
        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()

        final_stt, preview_stt = made[0], made[1]
        assert final_stt.max_attempts == MAX_ATTEMPTS == 3
        assert preview_stt.max_attempts == 1


class TestProviderRetryBudget:
    @pytest.mark.asyncio
    async def test_single_attempt_does_not_retry_on_429(self) -> None:
        from app.providers.stt import provider as stt_mod

        calls = 0

        def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(429, text="rate limited")

        transport = httpx.MockTransport(_handler)

        class _Ctx:
            async def __aenter__(self):
                self.client = httpx.AsyncClient(transport=transport)
                return self.client

            async def __aexit__(self, *exc):
                await self.client.aclose()

        p = stt_mod.GroqWhisperSTTProvider(api_key="k", base_url="https://stt.test/v1", model="m")
        p.max_attempts = 1
        with patch.object(stt_mod, "shared_http", lambda: _Ctx()):
            with pytest.raises(stt_mod.STTRequestError):
                await p.transcribe(b"RIFF....WAVEdata", mime_type="audio/wav")
        assert calls == 1
