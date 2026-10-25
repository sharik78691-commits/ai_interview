"""Regressions found by replaying a scripted interview through /ws/interview.

* A clip with no transcript (cough, keyboard burst, STT error) sent nothing
  that freed the client's single clip slot, so later clips queued ~10 s and
  newer ones replaced older ones — half a question was lost.
* Whisper sound tags ("*thud*") leaked into questions.
* A set-up statement ("In your last project you used Kafka.") was answered on
  its own and split from the question that followed it.
* "Also, …" after an answered question was glued onto that question.
* A rate-limited model went straight to template guidance.
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.ai_response import AIInterviewResponse
from app.providers.llm.base import LLMProvider
from app.providers.llm.provider import LLMQuotaError, MockLLMProvider
from app.providers.stt.provider import strip_sound_tags
from app.services.ai_service import AIService
from app.services.question_service import (
    LiveTurnTracker,
    QuestionBuffer,
    is_context_statement,
)

CONTEXT = "In your last project you used Kafka."
QUESTION = "How did you handle duplicate messages?"


# ------------------------------------------------------------ sound tags


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("*thud*  Tell me about yourself.", "Tell me about yourself."),
        ("*gunshot*", ""),
        ("[Music]", ""),
        ("(coughs) What is a closure?", "What is a closure?"),
        ("What is dependency injection (DI)?", "What is dependency injection (DI)?"),
        ("", ""),
    ],
)
def test_strip_sound_tags(raw, clean) -> None:
    assert strip_sound_tags(raw) == clean


# ------------------------------------------------------ set-up statements


@pytest.mark.parametrize(
    "text, expected",
    [
        (CONTEXT, True),
        ("Your resume says you led a team of five.", True),
        ("Tell me about yourself.", False),  # a prompt, answered at once
        ("What is a deadlock?", False),
        ("Okay, great.", False),  # backchannel, not context
        ("Can you explain the difference between", False),  # half question
        ("Aaj hum log system design round start karenge pehle", False),  # no full stop
    ],
)
def test_is_context_statement(text, expected) -> None:
    assert is_context_statement(text) is expected


def test_clip_lane_stitches_context_onto_the_next_question() -> None:
    b = QuestionBuffer()
    assert b.push_complete(CONTEXT, now=0.0) is None
    assert b.holding_partial
    assert b.push_complete("Okay.", now=1.0) is None  # backchannel keeps it waiting
    assert b.push_complete(QUESTION, now=2.0) == f"{CONTEXT} {QUESTION}"


def test_clip_lane_answers_context_alone_when_no_question_follows() -> None:
    b = QuestionBuffer()
    b.push_complete(CONTEXT, now=0.0)
    assert b.flush_expired(now=1.0) is None
    assert b.flush_expired(now=QuestionBuffer.HOLD_MS / 1000 + 0.1) == CONTEXT


def test_clip_lane_still_answers_a_real_question_at_once() -> None:
    assert QuestionBuffer().push_complete("Tell me about yourself.", now=0.0) == "Tell me about yourself."


def test_live_lane_stitches_context_onto_the_next_question() -> None:
    t = LiveTurnTracker()
    assert t.update(CONTEXT, True, now=0.0) is None
    assert t.carrying and t.carry_window == LiveTurnTracker.CONTEXT_CARRY_S
    assert t.deciding(now=1.0)  # the clip lane must leave this speech to the live lane
    t.update("How did you", False, now=0.6)
    assert t.update(QUESTION, True, now=1.4) == f"{CONTEXT} {QUESTION}"


def test_live_lane_answers_context_alone_after_its_window() -> None:
    t = LiveTurnTracker()
    t.update(CONTEXT, True, now=0.0)
    assert t.flush_carry(now=1.0) is None
    assert t.flush_carry(now=LiveTurnTracker.CONTEXT_CARRY_S + 0.1) == CONTEXT


def test_live_lane_keeps_a_new_question_separate_from_the_answered_one() -> None:
    t = LiveTurnTracker()
    t.update("What is polymorphism?", False, now=0.0)
    assert t.pause(now=0.5) == "What is polymorphism?"
    both = "What is polymorphism? Also, how does garbage collection work in Java?"
    t.update(both, False, now=1.5)
    assert t.update(both, True, now=2.5) == "Also, how does garbage collection work in Java?"


def test_live_lane_still_gives_a_follow_up_its_context() -> None:
    t = LiveTurnTracker()
    t.update("How would you design a URL shortener?", False, now=0.0)
    assert t.pause(now=0.5)
    both = "How would you design a URL shortener? And how would you scale it to millions?"
    t.update(both, False, now=1.5)
    answered = t.update(both, True, now=2.5)
    assert answered is not None and answered.startswith("How would you design a URL shortener?")


# ------------------------------------------------------ AI model fallback


def _answer(q: str) -> AIInterviewResponse:
    return AIInterviewResponse(
        question=q, questionType="technical", answerPoints=["Real point"], keyDetails=[],
        example="", keyTakeaway="", followUps=[],
    )


class _Provider(LLMProvider):
    def __init__(self, name: str, fail: bool = False) -> None:
        self.name, self.model, self.fail = name, f"{name}-model", fail
        self.last_error_category = "rate_limit" if fail else "ok"
        self.calls = 0

    async def analyze_question(self, resume, jd, question, response_length="medium", history=None):
        self.calls += 1
        if self.fail:
            raise LLMQuotaError("LLM rate limit reached")
        return _answer(question)


async def test_rate_limited_model_falls_back_to_another_model() -> None:
    primary, backup = _Provider("primary", fail=True), _Provider("backup")
    svc = AIService(provider_factory=lambda _k: primary, fallback_factory=lambda _k: backup)
    out = await svc.analyze("What is a deadlock?")
    assert out.answerPoints == ["Real point"]  # not template guidance
    assert svc.last_warning is None
    assert svc.last_provider == "backup" and svc.last_model == "backup-model"
    assert svc.last_fallback["model"] == "primary-model"
    assert svc.last_fallback["usage"]["error_category"] == "rate_limit"


async def test_template_guidance_only_when_no_fallback_can_answer() -> None:
    primary = _Provider("primary", fail=True)
    for fallback in (None, _Provider("backup", fail=True), MockLLMProvider()):
        svc = AIService(provider_factory=lambda _k: primary, fallback_factory=lambda _k, f=fallback: f)
        out = await svc.analyze("What is a deadlock?")
        assert svc.last_warning and "rate limit" in svc.last_warning
        assert svc.last_fallback is None
        assert out.answerPoints  # template guidance keeps the interview going


async def test_healthy_model_never_calls_the_fallback() -> None:
    backup = _Provider("backup")
    svc = AIService(provider_factory=lambda _k: _Provider("primary"), fallback_factory=lambda _k: backup)
    await svc.analyze("What is a deadlock?")
    assert backup.calls == 0 and svc.last_fallback is None


# -------------------------------------------------------------- clip_done


def _auth(client) -> None:
    client.post(
        "/api/auth/register",
        json={"email": "clipdone@example.com", "password": "Passw0rd123", "name": "WS"},
    )


@pytest.mark.parametrize("stt_text", ["", "*thud*", "Thank you."])
@patch("app.api.websocket.get_stt_provider")
def test_clip_without_transcript_ends_with_clip_done(stt_factory, stt_text) -> None:
    wav = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 64
    stt_factory.return_value = type("FakeSTT", (), {"transcribe": AsyncMock(return_value=stt_text)})()
    with TestClient(app) as client:
        _auth(client)
        with client.websocket_connect("/ws/interview") as ws:
            ws.receive_json()
            ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/wav"})
            ws.send_bytes(wav)
            ws.send_json({"type": "audio_flush", "mimeType": "audio/wav"})
            kinds = []
            for _ in range(6):
                msg = ws.receive_json()
                kinds.append(msg.get("type"))
                if msg.get("type") == "clip_done":
                    break
    assert "clip_done" in kinds
    assert "stt_transcript" not in kinds


@patch("app.api.websocket.get_stt_provider")
def test_clip_with_transcript_does_not_send_clip_done(stt_factory) -> None:
    wav = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 64
    stt_factory.return_value = type(
        "FakeSTT", (), {"transcribe": AsyncMock(return_value="Okay, great.")}
    )()
    with TestClient(app) as client:
        _auth(client)
        with client.websocket_connect("/ws/interview") as ws:
            ws.receive_json()
            ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/wav"})
            ws.send_bytes(wav)
            ws.send_json({"type": "audio_flush", "mimeType": "audio/wav"})
            kinds = []
            for _ in range(4):
                msg = ws.receive_json()
                kinds.append(msg.get("type"))
                if msg.get("type") == "status" and "waiting for a complete" in msg.get("message", ""):
                    break
            ws.send_json({"type": "ping"})
            kinds.append(ws.receive_json().get("type"))
    assert "stt_transcript" in kinds
    assert "clip_done" not in kinds
