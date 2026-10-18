"""Realtime-pipeline latency regressions (mocked providers — no network).

Before the concurrency fix the WebSocket receive loop AWAITED every STT and
LLM call inline, so while an answer was generating the socket could not read
anything: pings, model switches and — worst of all — the NEXT audio clip all
queued behind up to minutes of provider I/O. Users experienced this as "the
LLM answers late" and "voice-to-text takes forever to load".

These tests pin the fixed behaviour:
1. ping -> pong arrives WHILE an answer is still generating.
2. an audio clip transcribes WHILE an answer is still generating.
3. ping -> pong arrives WHILE a clip is still transcribing.
4. a duplicate question arriving in flight never spends a second AI call.
5. STT/LLM retry budgets stay bounded for live use.
"""
import asyncio
from unittest.mock import AsyncMock, patch

from app.models.ai_response import AIInterviewResponse


def _auth(client) -> None:
    client.post(
        "/api/auth/register",
        json={"email": "ws-latency@example.com", "password": "Passw0rd123", "name": "WS"},
    )


def _fake_guidance(question: str = "Can you explain the Saga pattern?") -> AIInterviewResponse:
    return AIInterviewResponse(
        question=question,
        questionType="technical",
        answerPoints=["Define Saga", "Explain compensation"],
        followUpQuestions=["Choreography vs orchestration?"],
        responseLength="medium",
    )


def _slow_analyze(ai_cls, delay: float, question: str = "Can you explain the Saga pattern?"):
    """AIService.analyze that keeps the answer in flight for ``delay`` seconds."""

    async def _analyze(q, model_key=None, on_delta=None, **kwargs):
        await asyncio.sleep(delay)
        return _fake_guidance(question)

    ai_cls.return_value.analyze = AsyncMock(side_effect=_analyze)
    ai_cls.return_value.last_warning = None


def _recv_until(ws, want: str, limit: int = 20) -> list[dict]:
    frames: list[dict] = []
    for _ in range(limit):
        msg = ws.receive_json()
        frames.append(msg)
        if msg.get("type") == want:
            return frames
    raise AssertionError(f"never received message type {want!r}: {frames}")


class TestReceiveLoopStaysResponsive:
    @patch("app.api.websocket.AIService")
    def test_ping_answered_while_answer_is_generating(self, ai_cls) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        _slow_analyze(ai_cls, delay=1.0)

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()  # handshake
                ws.send_json(
                    {"type": "interviewer_question", "text": "Can you explain the Saga pattern?"}
                )
                # Ping WHILE the LLM call is in flight. Inline await meant the
                # pong could only come after ai_guidance; now it must not.
                ws.send_json({"type": "ping"})
                frames = _recv_until(ws, "ai_guidance")

        types = [m.get("type") for m in frames]
        assert "pong" in types, types
        assert types.index("pong") < types.index("ai_guidance"), types
        assert ai_cls.return_value.analyze.await_count == 1

    @patch("app.api.websocket.get_stt_provider")
    @patch("app.api.websocket.AIService")
    def test_clip_transcribes_while_answer_is_generating(self, ai_cls, stt_factory) -> None:
        """The next interviewer clip must not wait for the current answer."""
        from fastapi.testclient import TestClient

        from app.main import app

        _slow_analyze(ai_cls, delay=1.0, question="What is DI?")
        stt_factory.return_value = type(
            "FakeSTT",
            (),
            {"transcribe": AsyncMock(return_value="What is dependency injection?")},
        )()

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()  # handshake
                # Q1 starts a slow answer...
                ws.send_json(
                    {"type": "interviewer_question", "text": "Can you explain the Saga pattern?"}
                )
                # ...and a second question arrives as audio WHILE Q1 generates.
                ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/webm"})
                ws.send_bytes(b"\x1a\x45\xdf\xa3fake-webm-bytes")
                ws.send_json({"type": "audio_flush"})
                frames = _recv_until(ws, "ai_guidance")

        types = [m.get("type") for m in frames]
        assert "stt_transcript" in types, types
        assert types.index("stt_transcript") < types.index("ai_guidance"), types
        transcripts = [m["text"] for m in frames if m.get("type") == "stt_transcript"]
        assert "What is dependency injection?" in transcripts
        assert stt_factory.return_value.transcribe.await_count == 1

    @patch("app.api.websocket.get_stt_provider")
    @patch("app.api.websocket.AIService")
    def test_ping_answered_while_clip_is_transcribing(self, ai_cls, stt_factory) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        async def _slow_transcribe(*args, **kwargs):
            await asyncio.sleep(1.0)
            return "Tell me about yourself."

        stt_factory.return_value = type(
            "FakeSTT", (), {"transcribe": AsyncMock(side_effect=_slow_transcribe)}
        )()
        ai_cls.return_value.analyze = AsyncMock(return_value=_fake_guidance("Tell me about yourself."))
        ai_cls.return_value.last_warning = None

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()  # handshake
                ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/webm"})
                ws.send_bytes(b"\x1a\x45\xdf\xa3fake-webm-bytes")
                ws.send_json({"type": "audio_flush"})
                # Ping WHILE the clip is still at the STT provider.
                ws.send_json({"type": "ping"})
                frames = _recv_until(ws, "ai_guidance")

        types = [m.get("type") for m in frames]
        assert "pong" in types, types
        assert types.index("pong") < types.index("stt_transcript"), types


class TestInFlightDuplicateGuard:
    @patch("app.api.websocket.AIService")
    def test_duplicate_during_generation_spends_one_ai_call(self, ai_cls) -> None:
        """Mic fragment + debounced force detector racing must not double-bill."""
        from fastapi.testclient import TestClient

        from app.main import app

        _slow_analyze(ai_cls, delay=1.0)

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()  # handshake
                ws.send_json(
                    {"type": "transcript", "text": "Can you explain the Saga pattern?"}
                )
                # Same question again WHILE the first answer is generating.
                ws.send_json(
                    {"type": "force", "text": "Can you explain the Saga pattern?"}
                )
                frames = _recv_until(ws, "ai_guidance")

        types = [m.get("type") for m in frames]
        guidance = [m for m in frames if m.get("type") == "ai_guidance"]
        assert len(guidance) == 1, types
        # The in-flight guard must reply BEFORE the answer finishes — a status
        # after ai_guidance would mean the duplicate was only caught by the
        # 30s window AFTER wasting a queue slot.
        dup_statuses = [
            m for m in frames
            if m.get("type") == "status" and "Already answered" in str(m.get("message", ""))
        ]
        assert dup_statuses, types
        assert frames.index(dup_statuses[0]) < types.index("ai_guidance"), types
        assert ai_cls.return_value.analyze.await_count == 1


class TestSplitQuestionStitching:
    """A pause mid-question splits the clip in two. The halves must rejoin.

    Regression for "the AI answers a fragment, then its tail": the silence gate
    cuts at ~1s, so any real mid-question pause produces TWO clips. Answering
    the first one alone answers "Can you explain the difference between" —
    which is what users saw as the AI replying to random/partial questions.
    """

    @staticmethod
    def _stt_by_clip_text(mapping: dict[bytes, str]):
        """STT stub keyed on the clip bytes, so clip ordering never matters.

        AsyncMock on purpose: a plain ``async def`` in a ``type(...)`` namespace
        is a descriptor, so it binds ``self`` as the first argument and the real
        clip silently lands in ``mime_type`` — the lookup then throws inside the
        background task and the test hangs forever waiting for a frame.
        """

        async def _transcribe(clip, mime_type=None):
            for suffix, text in mapping.items():
                if clip.endswith(suffix):
                    return text
            raise AssertionError(f"unexpected clip {clip!r}")

        return type("FakeSTT", (), {"transcribe": AsyncMock(side_effect=_transcribe)})()

    @staticmethod
    def _recv_with_keepalive(ws, want: str, limit: int = 40) -> list[dict]:
        """Read frames until ``want``, pinging so a lost answer FAILS fast.

        ``receive_json`` blocks indefinitely, so a broken detection path would
        hang the suite instead of reporting a failure. Every read that gets no
        closer re-pings, and a live server always answers with a pong.
        """
        frames: list[dict] = []
        for i in range(limit):
            if i % 3 == 2:
                ws.send_json({"type": "ping"})
            msg = ws.receive_json()
            frames.append(msg)
            if msg.get("type") == want:
                return frames
        raise AssertionError(f"never received message type {want!r}: {frames}")

    @patch("app.api.websocket.get_stt_provider")
    @patch("app.api.websocket.AIService")
    def test_two_clips_of_one_question_produce_one_stitched_answer(self, ai_cls, stt_factory) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        stt_factory.return_value = self._stt_by_clip_text(
            {
                b"part-one": "Can you explain the difference between",
                b"part-two": "a saga and two phase commit?",
            }
        )
        ai_cls.return_value.analyze = AsyncMock(return_value=_fake_guidance("stitched"))
        ai_cls.return_value.last_warning = None

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()  # handshake
                for tag in (b"part-one", b"part-two"):
                    ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/webm"})
                    ws.send_bytes(b"\x1a\x45\xdf\xa3" + tag)
                    ws.send_json({"type": "audio_flush"})
                frames = self._recv_with_keepalive(ws, "ai_guidance")

        asked = [c.args[0] for c in ai_cls.return_value.analyze.await_args_list]
        # Exactly ONE call, with the WHOLE question — not a fragment plus a tail.
        assert ai_cls.return_value.analyze.await_count == 1, asked
        assert asked[0] == (
            "Can you explain the difference between a saga and two phase commit?"
        )
        types = [m.get("type") for m in frames]
        assert types.count("ai_guidance") == 1, types

    @patch("app.api.websocket.get_stt_provider")
    @patch("app.api.websocket.AIService")
    def test_held_question_is_flushed_when_the_interviewer_stops(self, ai_cls, stt_factory) -> None:
        """Abandoning a sentence mid-way must not LOSE the question."""
        from fastapi.testclient import TestClient

        from app.services.question_service import QuestionBuffer
        from app.main import app

        stt_factory.return_value = self._stt_by_clip_text(
            {b"only": "How would you design a rate limiter for"}
        )
        ai_cls.return_value.analyze = AsyncMock(return_value=_fake_guidance("flushed"))
        ai_cls.return_value.last_warning = None

        # Shrink the hold window so the test does not sleep for 5s.
        with patch.object(QuestionBuffer, "HOLD_MS", 300):
            with patch("app.main.app"), TestClient(app) as client:
                _auth(client)
                with client.websocket_connect("/ws/interview") as ws:
                    ws.receive_json()  # handshake
                    ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/webm"})
                    ws.send_bytes(b"\x1a\x45\xdf\xa3" + b"only")
                    ws.send_json({"type": "audio_flush"})
                    # No second clip ever arrives -> the flush timer must fire.
                    frames = self._recv_with_keepalive(ws, "ai_guidance")

        asked = [c.args[0] for c in ai_cls.return_value.analyze.await_args_list]
        assert ai_cls.return_value.analyze.await_count == 1, asked
        assert asked[0] == "How would you design a rate limiter for"

    @patch("app.api.websocket.get_stt_provider")
    @patch("app.api.websocket.AIService")
    def test_backchannel_clip_spends_no_ai_call(self, ai_cls, stt_factory) -> None:
        """"Okay, great, thanks for that." used to burn a full LLM round-trip."""
        from fastapi.testclient import TestClient

        from app.main import app

        stt_factory.return_value = self._stt_by_clip_text(
            {b"noise": "Okay, great, thanks for that."}
        )
        ai_cls.return_value.analyze = AsyncMock(return_value=_fake_guidance("should not fire"))
        ai_cls.return_value.last_warning = None

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()  # handshake
                ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/webm"})
                ws.send_bytes(b"\x1a\x45\xdf\xa3" + b"noise")
                ws.send_json({"type": "audio_flush"})
                # The UI must be told to keep waiting, and no answer may start.
                # Scan for the DETECTION outcome specifically — the handshake
                # also emits a "listening" status, which is not the signal.
                frames: list[dict] = []
                for _ in range(20):
                    msg = ws.receive_json()
                    frames.append(msg)
                    if (
                        msg.get("type") == "status"
                        and "waiting for a complete question" in str(msg.get("message", ""))
                    ):
                        break
                waiting = [
                    m for m in frames
                    if m.get("type") == "status"
                    and "waiting for a complete question" in str(m.get("message", ""))
                ]
                assert waiting, frames

        assert ai_cls.return_value.analyze.await_count == 0


class TestRetryBudgetsAreLiveFriendly:
    async def test_stt_per_attempt_timeout_is_bounded(self) -> None:
        """A hung STT socket must fail in <=30s, not a minute."""
        from app.providers.stt.provider import (
            GroqWhisperSTTProvider,
            REQUEST_TIMEOUT_SECONDS,
        )

        assert REQUEST_TIMEOUT_SECONDS <= 30.0

        p = GroqWhisperSTTProvider(api_key="k", base_url="https://x/v1", model="whisper")
        seen: dict = {}

        response = type(
            "R",
            (),
            {
                "status_code": 200,
                "json": lambda self: {"text": "hello there"},
                "text": "",
                "headers": {},
            },
        )()

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, **kwargs):
                seen.update(kwargs)
                return response

        with patch("app.providers.stt.provider.shared_http", return_value=FakeClient()):
            assert await p.transcribe(b"wav-bytes") == "hello there"
        assert seen["timeout"] <= 30.0

    def test_stt_backoff_stays_short(self) -> None:
        from app.providers.stt.provider import BACKOFF_SECONDS, RETRY_AFTER_CAP_SECONDS

        assert max(BACKOFF_SECONDS) <= 2.0
        assert RETRY_AFTER_CAP_SECONDS <= 10.0

    def test_llm_retry_backoff_stays_short(self) -> None:
        from app.providers.llm.provider import RETRY_BACKOFF_BASE

        # The old ladder slept 1.5s then 3.0s between pre-delta retries.
        assert RETRY_BACKOFF_BASE <= 1.0
