"""Answering from the live (word-by-word) transcript — mocked, no network.

Before: the interviewer's question was on screen word by word, yet the answer
only started after the clip's 0.85 s silence cut + upload + batch STT
(~1-2.6 s) — 2-3.5 s after the last word, plus up to 5 s more when the clip
boundary split the question. Now the live text itself is judged the way a
person judges a turn: a finished sentence + a short pause = go.

Pinned:
1. a closed sentence is answered at the short pause; the turn's final never
   answers it again;
2. interim words (interviewer still talking) are never answered;
3. a complete but unpunctuated prompt waits one beat, and new words cancel it;
4. a sentence left hanging is carried and stitched with its second half;
5. the clip of speech the live lane answered never answers it a second time,
   even when the two engines word it differently;
6. speech the live stream did not hear is still answered from its clip.
"""
import asyncio
from unittest.mock import AsyncMock, patch

from app.models.ai_response import AIInterviewResponse
from app.providers.stt import live_stream as ls
from app.services.question_service import (
    LiveTurnTracker,
    awaits_continuation,
    same_utterance,
)
from tests.test_stt_live_stream import FakeLiveSocket, _final, _interim, ws_session


class TestLiveTurnTracker:
    def test_closed_sentence_answers_at_the_pause_once(self) -> None:
        t = LiveTurnTracker()
        assert t.update("What is a", False, now=0.0) is None
        assert t.update("What is a deadlock?", False, now=0.5) is None
        assert t.pause(now=1.0) == "What is a deadlock?"
        # The provider closes the same turn a moment later: already answered.
        assert t.update("What is a deadlock?", True, now=1.8) is None

    def test_interim_words_never_answer(self) -> None:
        t = LiveTurnTracker()
        for i, text in enumerate(["Tell me", "Tell me about your", "Tell me about your project."]):
            assert t.update(text, False, now=i * 0.5) is None
        assert t.recheck is False

    def test_unpunctuated_prompt_waits_one_beat(self) -> None:
        t = LiveTurnTracker()
        t.update("Tell me about your last project", False, now=0.0)
        assert t.pause(now=0.5) is None
        assert t.recheck is True
        assert t.settle(t.gen, now=1.1) == "Tell me about your last project"

    def test_new_words_cancel_the_beat(self) -> None:
        t = LiveTurnTracker()
        t.update("Tell me about your last project", False, now=0.0)
        t.pause(now=0.5)
        gen = t.gen
        t.update("Tell me about your last project and the challenges", False, now=0.9)
        assert t.settle(gen, now=1.1) is None

    def test_turn_close_answers_an_unpunctuated_prompt(self) -> None:
        t = LiveTurnTracker()
        t.update("Explain the CAP theorem", True, now=0.0)
        assert t.pause() is None  # closed turns are not re-judged
        t2 = LiveTurnTracker()
        assert t2.update("Explain the CAP theorem", True, now=0.0) == "Explain the CAP theorem"

    def test_hanging_turn_is_stitched_with_its_second_half(self) -> None:
        t = LiveTurnTracker()
        assert t.update("Can you explain the difference between", True, now=0.0) is None
        assert t.carrying and t.deciding(now=1.0)
        assert t.update("a saga and two phase commit?", True, now=2.0) == (
            "Can you explain the difference between a saga and two phase commit?"
        )
        assert not t.carrying

    def test_verb_waiting_for_its_object_is_not_answered(self) -> None:
        t = LiveTurnTracker()
        t.update("Can you explain", False, now=0.0)
        assert t.pause(now=0.5) is None and not t.recheck
        assert t.update("Can you explain", True, now=1.3) is None
        assert t.update("how a hash map works?", True, now=2.5) == (
            "Can you explain how a hash map works?"
        )

    def test_abandoned_half_question_is_flushed(self) -> None:
        t = LiveTurnTracker()
        t.update("How would you design a rate limiter for", True, now=0.0)
        assert t.flush_carry(now=1.0) is None
        assert t.flush_carry(now=t.CARRY_S + 0.1) == "How would you design a rate limiter for"
        assert t.flush_carry(now=t.CARRY_S + 9) is None  # only once

    def test_backchannel_turn_is_ignored(self) -> None:
        t = LiveTurnTracker()
        assert t.update("Okay, great.", True, now=0.0) is None
        assert not t.carrying and not t.deciding(now=0.1)

    def test_afterthought_is_not_a_second_question(self) -> None:
        t = LiveTurnTracker()
        t.update("What is Kafka?", False, now=0.0)
        assert t.pause(now=0.5) == "What is Kafka?"
        t.update("What is Kafka? in Java?", False, now=1.0)
        assert t.update("What is Kafka? in Java?", True, now=2.0) is None

    def test_real_follow_on_is_answered_with_its_context(self) -> None:
        t = LiveTurnTracker()
        t.update("What is Kafka?", False, now=0.0)
        assert t.pause(now=0.5) == "What is Kafka?"
        t.update("What is Kafka? And how does it differ from RabbitMQ?", False, now=1.5)
        assert t.pause(now=2.0) == "What is Kafka? And how does it differ from RabbitMQ?"

    def test_stale_open_turn_stops_claiming_speech(self) -> None:
        t = LiveTurnTracker()
        t.update("What is the difference between", False, now=0.0)
        assert t.deciding(now=1.0)
        assert not t.deciding(now=t.STALE_S + 0.1)  # stream died mid-turn

    def test_close_judges_an_open_turn(self) -> None:
        t = LiveTurnTracker()
        t.update("Describe a time you led a team", False, now=0.0)
        assert t.close(now=0.5) == "Describe a time you led a team"


class TestHelpers:
    def test_awaits_continuation(self) -> None:
        assert awaits_continuation("Can you explain")
        assert awaits_continuation("tell me")
        assert awaits_continuation("the difference between")
        assert not awaits_continuation("Can you explain?")
        assert not awaits_continuation("Tell me about the design")
        assert not awaits_continuation("What is your name")

    def test_same_utterance_across_engines(self) -> None:
        assert same_utterance(
            "What is the difference between a saga and two phase commit?",
            "the difference between a Saga and 2 phase commit",
        )
        # Half of the question (one clip) is still the same speech.
        assert same_utterance(
            "Can you explain the difference between a saga and two phase commit?",
            "Can you explain the difference between",
        )
        assert not same_utterance("What is Java?", "What is Python?")
        assert not same_utterance("What is Java?", "Can you do it?")  # no content words


def _auth(client) -> None:
    client.post(
        "/api/auth/register",
        json={"email": "ws-live-answers@example.com", "password": "Passw0rd123", "name": "WS"},
    )


def _guidance(q: str) -> AIInterviewResponse:
    return AIInterviewResponse(
        question=q, questionType="technical", answerPoints=["a"], responseLength="medium"
    )


def _recv_until(ws, pred, limit: int = 40) -> list[dict]:
    frames: list[dict] = []
    for i in range(limit):
        if i % 3 == 2:
            ws.send_json({"type": "ping"})
        msg = ws.receive_json()
        frames.append(msg)
        if pred(msg):
            return frames
    raise AssertionError(f"condition never met: {frames}")


def _waiting(m: dict) -> bool:
    return m.get("type") == "status" and "waiting for a complete question" in str(m.get("message"))


# prewarm_stt opens a REAL connection to the STT host at capture start; a
# short test can end while it is still connecting, and the test client's
# teardown then surfaces the cancelled task as a CancelledError.
@patch("app.api.websocket.prewarm_stt", new=AsyncMock())
class TestWebSocketLiveAnswers:
    @staticmethod
    def _slow_stt(text: str, delay: float):
        async def _transcribe(*args, **kwargs):
            await asyncio.sleep(delay)
            return text

        return type("FakeSTT", (), {"transcribe": AsyncMock(side_effect=_transcribe)})()

    @patch("app.api.websocket.get_stt_provider")
    @patch("app.api.websocket.AIService")
    def test_live_turn_answers_before_the_clip_and_only_once(self, ai_cls, stt_factory) -> None:
        from fastapi.testclient import TestClient

        from app.core.config import get_settings
        from app.main import app

        sock = FakeLiveSocket([_interim("What is a deadlock"), _final("What is a deadlock?")])
        # The batch engine words the SAME speech differently ("dead lock"):
        # text matching alone could not tell it is the question answered.
        stt_factory.return_value = self._slow_stt("So what's a dead lock?", delay=1.5)
        ai_cls.return_value.analyze = AsyncMock(return_value=_guidance("What is a deadlock?"))
        ai_cls.return_value.last_warning = None

        # The turn close must win over the settle beat however slow the test
        # host is (otherwise the unpunctuated interim is what gets answered).
        with patch.object(get_settings(), "gemini_api_key", "test-key"), patch.object(
            ls, "_connect", lambda key: sock
        ), patch("app.api.websocket.LIVE_SETTLE_SECONDS", 30.0):
            with patch("app.main.app"), TestClient(app) as client:
                _auth(client)
                with ws_session(client) as ws:
                    hello = ws.receive_json()
                    assert hello.get("liveAnswers") is True
                    ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/wav"})
                    ws.send_bytes(ls.LIVE_PCM_TAG + b"\x01\x00" * 800)
                    _recv_until(ws, lambda m: m.get("type") == "stt_live")
                    # The client cuts the clip, then the stream closes the turn.
                    ws.send_bytes(b"RIFF\x00\x00\x00\x00WAVEclip")
                    ws.send_json({"type": "audio_flush", "mimeType": "audio/wav"})
                    ws.send_bytes(ls.LIVE_PCM_TAG + b"\x02\x00" * 800)
                    frames = _recv_until(ws, lambda m: m.get("type") == "ai_guidance")
                    # The clip's final still shows, but answers nothing (a
                    # second ai_guidance ends the wait too, and fails below).
                    frames += _recv_until(
                        ws, lambda m: _waiting(m) or m.get("type") == "ai_guidance"
                    )

        types = [m.get("type") for m in frames]
        assert types.count("ai_guidance") == 1, types
        assert types.index("ai_guidance") < types.index("stt_transcript"), types
        asked = [c.args[0] for c in ai_cls.return_value.analyze.await_args_list]
        assert asked == ["What is a deadlock?"]

    @patch("app.api.websocket.get_stt_provider")
    @patch("app.api.websocket.AIService")
    def test_speech_pause_answers_a_closed_sentence_without_any_clip(
        self, ai_cls, stt_factory
    ) -> None:
        from fastapi.testclient import TestClient

        from app.core.config import get_settings
        from app.main import app

        sock = FakeLiveSocket([_interim("What is dependency injection?"), _final("What is dependency injection?")])
        stt_factory.return_value = self._slow_stt("unused", delay=0)
        ai_cls.return_value.analyze = AsyncMock(return_value=_guidance("DI"))
        ai_cls.return_value.last_warning = None

        with patch.object(get_settings(), "gemini_api_key", "test-key"), patch.object(
            ls, "_connect", lambda key: sock
        ):
            with patch("app.main.app"), TestClient(app) as client:
                _auth(client)
                with ws_session(client) as ws:
                    ws.receive_json()
                    ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/wav"})
                    ws.send_bytes(ls.LIVE_PCM_TAG + b"\x01\x00" * 800)
                    _recv_until(ws, lambda m: m.get("type") == "stt_live")
                    ws.send_json({"type": "speech_pause", "responseLength": "short"})
                    _recv_until(ws, lambda m: m.get("type") == "ai_guidance")
                    # The turn's final arrives afterwards: no second answer.
                    ws.send_bytes(ls.LIVE_PCM_TAG + b"\x02\x00" * 800)
                    _recv_until(ws, lambda m: m.get("type") == "stt_live" and m.get("final"))
                    ws.send_json({"type": "ping"})
                    _recv_until(ws, lambda m: m.get("type") == "pong")

        asked = [c.args[0] for c in ai_cls.return_value.analyze.await_args_list]
        assert asked == ["What is dependency injection?"]
        stt_factory.return_value.transcribe.assert_not_awaited()

    @patch("app.api.websocket.get_stt_provider")
    @patch("app.api.websocket.AIService")
    def test_speech_the_stream_missed_is_answered_from_its_clip(self, ai_cls, stt_factory) -> None:
        from fastapi.testclient import TestClient

        from app.core.config import get_settings
        from app.main import app

        sock = FakeLiveSocket()  # connected, but hears nothing
        stt_factory.return_value = self._slow_stt("What is dependency injection?", delay=0)
        ai_cls.return_value.analyze = AsyncMock(return_value=_guidance("DI"))
        ai_cls.return_value.last_warning = None

        with patch.object(get_settings(), "gemini_api_key", "test-key"), patch.object(
            ls, "_connect", lambda key: sock
        ):
            with patch("app.main.app"), TestClient(app) as client:
                _auth(client)
                with ws_session(client) as ws:
                    ws.receive_json()
                    ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/wav"})
                    ws.send_bytes(b"RIFF\x00\x00\x00\x00WAVEclip")
                    ws.send_json({"type": "audio_flush", "mimeType": "audio/wav"})
                    _recv_until(ws, lambda m: m.get("type") == "ai_guidance")

        asked = [c.args[0] for c in ai_cls.return_value.analyze.await_args_list]
        assert asked == ["What is dependency injection?"]

    def test_live_answers_can_be_switched_off(self) -> None:
        from fastapi.testclient import TestClient

        from app.core.config import get_settings
        from app.main import app

        with patch.object(get_settings(), "gemini_api_key", "test-key"), patch.object(
            get_settings(), "stt_live_answers", False
        ), patch.object(ls, "_connect", lambda key: FakeLiveSocket()):
            with patch("app.main.app"), TestClient(app) as client:
                _auth(client)
                with ws_session(client) as ws:
                    hello = ws.receive_json()
        assert hello.get("liveStream") is True
        assert hello.get("liveAnswers") is False
