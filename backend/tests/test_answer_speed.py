"""Answer-speed regressions (mocked providers — no network).

Reported: "the transcript is slow to pick up the question, it gets stuck, and
the answer comes late". Measured 2026-10-26 on the real pipeline:

1. Gemini Live delivers a question's LAST words (often the "?") 0.3-0.7 s
   after speech ends — after the client's 0.45 s pause signal. Interim text
   was never re-judged, so the answer waited for the 0.85 s clip cut or the
   turn close (~1.4 s).
2. The default model (Cerebras gpt-oss) allows 5 requests/min. Every answer
   past that paid 429 + 0.5 s + 429 + 1 s + 429, then a NON-streamed fallback
   — several seconds of a blank card, once per question, for a whole minute.
3. Answers run one at a time: a new question waited for the previous answer
   to finish streaming (or its whole retry + fallback cycle) before starting.
"""
import asyncio
import time
from unittest.mock import AsyncMock, patch

from app.models.ai_response import AIInterviewResponse
from app.providers.llm import provider as llm_provider
from app.providers.llm.base import LLMProvider
from app.providers.llm.provider import (
    LLMQuotaError,
    OpenAICompatibleLLMProvider,
    is_rate_limited,
    mark_rate_limited,
)
from app.services.ai_service import AIService
from app.services.question_service import LiveTurnTracker

# ------------------------------------------------- 1. live lane: late words


class TestLateWordsAfterAPause:
    def test_question_mark_arriving_after_the_pause_answers_at_once(self) -> None:
        t = LiveTurnTracker()
        t.update("Can you explain the difference between a process and a", False, now=0.0)
        assert t.pause(now=0.45) is None  # "...and a" still hangs
        full = "Can you explain the difference between a process and a thread?"
        # Measured: this interim lands ~+0.5 s after the pause signal.
        assert t.update(full, False, now=0.95) == full
        assert t.update(full, True, now=1.5) is None  # the turn close: already answered

    def test_unpunctuated_late_words_get_the_settle_beat(self) -> None:
        t = LiveTurnTracker()
        t.update("What is dependency injection in", False, now=0.0)
        assert t.pause(now=0.45) is None and not t.recheck  # dangling "in"
        q = "What is dependency injection in Spring Boot"
        assert t.update(q, False, now=0.9) is None
        assert t.recheck is True
        assert t.settle(t.gen, now=1.5) == q

    def test_late_words_still_never_answer_a_hanging_sentence(self) -> None:
        t = LiveTurnTracker()
        t.update("Can you explain", False, now=0.0)
        t.pause(now=0.45)
        assert t.update("Can you explain the difference between", False, now=0.8) is None
        assert not t.recheck

    def test_words_long_after_the_pause_wait_for_the_turn(self) -> None:
        t = LiveTurnTracker()
        t.update("Can you explain the", False, now=0.0)
        t.pause(now=0.45)
        q = "Can you explain the CAP theorem?"
        assert t.update(q, False, now=0.45 + t.LATE_WORDS_S + 0.2) is None
        assert t.update(q, True, now=2.0) == q

    def test_without_a_pause_interim_words_never_answer(self) -> None:
        t = LiveTurnTracker()
        assert t.update("What is a deadlock?", False, now=0.0) is None
        assert t.update("What is a deadlock? And", False, now=0.3) is None

    def test_a_new_turn_forgets_the_old_pause(self) -> None:
        t = LiveTurnTracker()
        t.update("Okay, great.", False, now=0.0)
        t.pause(now=0.45)
        t.update("Okay, great.", True, now=1.3)
        # Next turn opens right away: its interims are speech, not lag.
        assert t.update("What is a closure?", False, now=1.4) is None


# ------------------------------------------- 2. rate-limited model fast path


class _Resp429:
    status_code = 429
    request = None
    text = '{"error": "rate limit"}'

    def __init__(self, retry_after: str | None = None) -> None:
        self.headers = {"retry-after": retry_after} if retry_after else {}

    async def aread(self) -> bytes:
        return b""


def _counting_stream_client(calls: list[int]):
    class _CM:
        async def __aenter__(self):
            calls.append(1)
            return _Resp429()

        async def __aexit__(self, *args):
            return False

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return _CM()

    return _Client()


def _real_provider(name: str = "fast-model") -> OpenAICompatibleLLMProvider:
    return OpenAICompatibleLLMProvider(
        api_key="k", base_url="https://example.test/v1", model="m", name=name
    )


async def test_429_with_a_fallback_fails_fast_and_cools_down() -> None:
    p = _real_provider()
    p.fail_fast_rate_limit = True
    calls: list[int] = []
    with patch("app.providers.llm.provider.shared_http", return_value=_counting_stream_client(calls)):
        t0 = time.perf_counter()
        try:
            await p.analyze_question_stream("", "", "What is REST?", "medium", AsyncMock())
            raise AssertionError("expected LLMQuotaError")
        except LLMQuotaError:
            pass
    assert calls == [1]  # no retries into the same per-minute window
    assert time.perf_counter() - t0 < 0.4  # no backoff sleeps
    assert is_rate_limited("fast-model")


async def test_429_without_a_fallback_still_retries() -> None:
    p = _real_provider()
    calls: list[int] = []
    with patch.object(llm_provider, "RETRY_BACKOFF_BASE", 0.0), patch(
        "app.providers.llm.provider.shared_http", return_value=_counting_stream_client(calls)
    ):
        try:
            await p.analyze_question_stream("", "", "What is REST?", "medium", AsyncMock())
        except LLMQuotaError:
            pass
    assert len(calls) == p.max_retries + 1
    assert is_rate_limited("fast-model")


def test_cooldown_honours_retry_after_within_bounds() -> None:
    now = time.monotonic()
    mark_rate_limited("a", "45")
    mark_rate_limited("b", "1")
    mark_rate_limited("c", "9999")
    mark_rate_limited("d", "soon")
    left = {k: v - now for k, v in llm_provider._rate_limited_until.items()}
    assert 44 < left["a"] < 46
    assert 4 < left["b"] < 6  # floor
    assert left["c"] <= llm_provider.RATE_LIMIT_COOLDOWN_MAX_S + 1
    assert abs(left["d"] - llm_provider.RATE_LIMIT_COOLDOWN_S) < 1
    llm_provider._rate_limited_until["a"] = time.monotonic() - 1
    assert not is_rate_limited("a")  # window over: the model is used again


def _answer(q: str) -> AIInterviewResponse:
    return AIInterviewResponse(question=q, questionType="technical", answerPoints=["P1", "P2"])


class _StreamProvider(LLMProvider):
    def __init__(self, name: str, fail: bool = False) -> None:
        self.name, self.model, self.fail = name, f"{name}-model", fail
        self.calls = 0
        self.stream_had_deltas = False
        self.last_error_category = "rate_limit" if fail else "ok"
        self.fail_fast_rate_limit = False

    async def analyze_question(self, resume, jd, question, response_length="medium", history=None):
        self.calls += 1
        if self.fail:
            raise LLMQuotaError("LLM rate limit reached")
        return _answer(question)

    async def analyze_question_stream(
        self, resume, jd, question, response_length="medium", on_delta=None, history=None
    ):
        self.calls += 1
        if self.fail:
            raise LLMQuotaError("LLM rate limit reached")
        for point in _answer(question).answerPoints:
            await on_delta(point)
        return _answer(question)


async def test_rate_limited_model_is_skipped_and_the_fallback_streams() -> None:
    primary, backup = _StreamProvider("primary"), _StreamProvider("backup")
    mark_rate_limited("primary")
    svc = AIService(provider_factory=lambda _k: primary, fallback_factory=lambda _k: backup)
    deltas: list[str] = []

    async def on_delta(p: str) -> None:
        deltas.append(p)

    out = await svc.analyze("What is a deadlock?", on_delta=on_delta)
    assert primary.calls == 0  # not one request into the 429 window
    assert deltas == ["P1", "P2"] and out.answerPoints == ["P1", "P2"]
    assert svc.last_provider == "backup" and svc.last_warning is None


async def test_429_switches_to_a_streamed_fallback() -> None:
    primary, backup = _StreamProvider("primary", fail=True), _StreamProvider("backup")
    svc = AIService(provider_factory=lambda _k: primary, fallback_factory=lambda _k: backup)
    deltas: list[str] = []

    async def on_delta(p: str) -> None:
        deltas.append(p)

    await svc.analyze("What is a deadlock?", on_delta=on_delta)
    assert primary.fail_fast_rate_limit is True
    assert deltas == ["P1", "P2"]  # bullets appear, not one late block
    assert svc.last_fallback["model"] == "primary-model"


async def test_without_a_fallback_the_model_keeps_its_retries() -> None:
    primary = _StreamProvider("primary")
    svc = AIService(provider_factory=lambda _k: primary, fallback_factory=lambda _k: None)
    await svc.analyze("What is a deadlock?", on_delta=AsyncMock())
    assert primary.fail_fast_rate_limit is False


# ------------------------------------- 3. newest question wins the worker

Q1 = "Can you explain the Saga pattern?"
Q2 = "What is dependency injection?"


def _auth(client) -> None:
    client.post(
        "/api/auth/register",
        json={"email": "ws-speed@example.com", "password": "Passw0rd123", "name": "WS"},
    )


def _recv_until(ws, pred, limit: int = 40) -> list[dict]:
    frames: list[dict] = []
    for _ in range(limit):
        msg = ws.receive_json()
        frames.append(msg)
        if pred(msg):
            return frames
    raise AssertionError(f"condition never met: {frames}")


def _guidance_for(q: str):
    return lambda m: m.get("type") == "ai_guidance" and m["data"]["question"] == q


class TestNewestQuestionWins:
    @patch("app.api.websocket.AIService")
    def test_new_question_supersedes_the_answer_in_progress(self, ai_cls) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        cancelled: list[str] = []

        async def _analyze(q, model_key=None, on_delta=None, **kwargs):
            if q == Q1:
                try:
                    await asyncio.sleep(30)  # a stalled / slow answer
                except asyncio.CancelledError:
                    cancelled.append(q)
                    raise
            return _answer(q)

        ai_cls.return_value.analyze = AsyncMock(side_effect=_analyze)
        ai_cls.return_value.last_warning = None

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()  # handshake
                ws.send_json({"type": "interviewer_question", "text": Q1})
                _recv_until(ws, lambda m: m.get("type") == "ai_stream_start")
                t0 = time.perf_counter()
                ws.send_json({"type": "interviewer_question", "text": Q2})
                frames = _recv_until(ws, _guidance_for(Q2))
                waited = time.perf_counter() - t0

        assert waited < 5, waited  # not behind Q1's 30 s
        assert cancelled == [Q1]
        answers = [m["data"]["question"] for m in frames if m.get("type") == "ai_guidance"]
        assert answers == [Q2]  # Q1 never finishes as a stale answer
        assert not [m for m in frames if m.get("type") == "error"]

    @patch("app.api.websocket.AIService")
    def test_a_repeat_never_cancels_the_answer_in_progress(self, ai_cls) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        cancelled: list[str] = []

        async def _analyze(q, model_key=None, on_delta=None, **kwargs):
            if q == Q2:
                try:
                    await asyncio.sleep(0.8)
                except asyncio.CancelledError:
                    cancelled.append(q)
                    raise
            return _answer(q)

        ai_cls.return_value.analyze = AsyncMock(side_effect=_analyze)
        ai_cls.return_value.last_warning = None

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()
                ws.send_json({"type": "interviewer_question", "text": Q1})
                _recv_until(ws, _guidance_for(Q1))
                ws.send_json({"type": "interviewer_question", "text": Q2})
                _recv_until(ws, lambda m: m.get("type") == "ai_stream_start")
                # Q1 again — the answer just delivered, so the worker would only
                # suppress it: it must not cancel Q2 on its way in.
                ws.send_json({"type": "interviewer_question", "text": Q1})
                frames = _recv_until(ws, _guidance_for(Q2))

        assert cancelled == []
        assert [m["data"]["question"] for m in frames if m.get("type") == "ai_guidance"] == [Q2]
