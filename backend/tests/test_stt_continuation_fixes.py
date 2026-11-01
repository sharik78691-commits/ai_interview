"""Interviewer-audio STT: a two-part question must be answered WHOLE.

Found 2026-10-31 by a real-time E2E run (TTS interview, Whisper on DeepInfra,
browser-faithful client): "Explain the Java collection framework" + 1 s pause
+ "and its hierarchy." The first clip was correctly held for the rest, yet
"Explain the Java collection framework" was answered ALONE and the second
half vanished. Two independent causes, plus a third STT defect:

1. During a continuing hold, QuestionBuffer skipped any clip that
   ``classify()`` called NOISE — and a continuation fragment ("and its
   hierarchy.", "with an example.") is noise when judged standalone.
2. The browser keeps one clip in flight, so a cut clip can wait in its queue
   while the NEXT clip's speech_start is already sent. The server took the
   audio_flush ARRIVAL as the cut time and reset "speaking" on it, so the
   still-talking interviewer looked quiet and the half question was answered.
3. An out-of-credits STT account (OpenAI 429 insufficient_quota) was retried
   for ~5 s and then reported as "rate limit — waiting and retrying"; a
   Gemini billing 403 was reported as a bad GEMINI_API_KEY.
"""
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.question_service import QuestionBuffer, is_backchannel
from tests.test_turn_taking import _asked, _clip, _run, _until

# ------------------------------------------------- 1. continuation fragments


@pytest.mark.parametrize(
    "rest",
    ["and its hierarchy.", "and its hierarchy", "hierarchy.", "with an example.",
     "and its time complexity.", "using Spring Boot."],
)
def test_continuing_hold_stitches_a_fragment_that_reads_as_noise_alone(rest) -> None:
    b = QuestionBuffer()
    b.hold_continuing("Explain the Java collection framework", now=0.0)
    out = b.push_complete(rest, now=1.0)
    stitched = f"Explain the Java collection framework {rest}"
    # Either answered now (terminated) or held as ONE stitched text — never dropped.
    assert out == stitched or (out is None and b.held_text == stitched)
    assert not b.holding_continuing  # the half on its own can no longer be released


@pytest.mark.parametrize(
    "reaction",
    ["okay", "Okay.", "Okay. Great.", "Okay, that makes sense.", "mm-hmm", "Thank you.",
     "right", "Can you hear me?"],
)
def test_continuing_hold_still_skips_pure_reactions(reaction) -> None:
    b = QuestionBuffer()
    b.hold_continuing("Explain the Java collection framework", now=0.0)
    assert b.push_complete(reaction, now=1.0) is None
    assert b.holding_continuing
    assert b.take_continuing() == "Explain the Java collection framework"


def test_is_backchannel_keeps_content_after_a_reaction() -> None:
    assert not is_backchannel("I see, and its hierarchy")
    assert not is_backchannel("right, and in Java")
    assert not is_backchannel("in Java?")
    assert is_backchannel("")


@patch("app.api.websocket.TURN_END_GRACE_SECONDS", 0.3)
@patch("app.api.websocket.TURN_QUIET_SECONDS", 0.3)
@patch("app.api.websocket.get_stt_provider")
@patch("app.api.websocket.AIService")
def test_socket_answers_held_question_with_its_noise_like_second_half(ai_cls, stt_factory) -> None:
    def script(ws):
        _clip(ws, b"first-half")
        ws.send_json({"type": "speech_start"})
        frames = _until(ws, lambda m: "rest of the question" in str(m.get("message", "")))
        _clip(ws, b"second-half")
        return frames + _until(ws, lambda m: m.get("type") == "ai_guidance")

    _run(ai_cls, stt_factory, ["Explain the Java collection framework", "and its hierarchy."], script)
    assert _asked(ai_cls) == ["Explain the Java collection framework and its hierarchy."]


# ------------------------------------------- 2. clip queued in the browser


@patch("app.api.websocket.TURN_END_GRACE_SECONDS", 0.3)
@patch("app.api.websocket.TURN_QUIET_SECONDS", 0.3)
@patch("app.api.websocket.get_stt_provider")
@patch("app.api.websocket.AIService")
def test_next_clip_speech_before_queued_flush_holds_and_answers_whole(ai_cls, stt_factory) -> None:
    """Exact E2E order: clip 1 spoken, clip 2 (the rest) starts while clip 1
    still waits in the browser queue behind a slow transcription."""

    def script(ws):
        ws.send_json({"type": "speech_start", "clip": 1})
        time.sleep(2.0)
        cut_1 = time.monotonic()  # 0.85 s of quiet ends clip 1 (queued: busy)
        time.sleep(0.7)  # resumes ~0.3 s later; the signal needs 0.4 s of voice
        ws.send_json({"type": "speech_start", "clip": 2})  # continuation begins
        time.sleep(0.6)
        # Clip 1 leaves the browser queue only now, ~1.3 s after its cut.
        ws.send_bytes(b"\x1a\x45\xdf\xa3first-half")
        ws.send_json({"type": "audio_flush", "clip": 1,
                      "cutAgoMs": round((time.monotonic() - cut_1) * 1000)})
        frames = _until(ws, lambda m: m.get("type") == "ai_guidance"
                        or "rest of the question" in str(m.get("message", "")))
        assert frames[-1].get("type") != "ai_guidance", "half question answered"
        ws.send_bytes(b"\x1a\x45\xdf\xa3second-half")
        ws.send_json({"type": "audio_flush", "clip": 2, "cutAgoMs": 0})
        return frames + _until(ws, lambda m: m.get("type") == "ai_guidance")

    _run(ai_cls, stt_factory, ["Explain the Java collection framework.", "and its hierarchy."], script)
    assert _asked(ai_cls) == ["Explain the Java collection framework. and its hierarchy."]


@patch("app.api.websocket.TURN_END_GRACE_SECONDS", 0.3)
@patch("app.api.websocket.TURN_QUIET_SECONDS", 0.3)
@patch("app.api.websocket.get_stt_provider")
@patch("app.api.websocket.AIService")
def test_numbered_clip_without_later_speech_is_answered_promptly(ai_cls, stt_factory) -> None:
    def script(ws):
        ws.send_json({"type": "speech_start", "clip": 7})
        ws.send_bytes(b"\x1a\x45\xdf\xa3whole")
        ws.send_json({"type": "audio_flush", "clip": 7, "cutAgoMs": 40})
        return _until(ws, lambda m: m.get("type") == "ai_guidance")

    _run(ai_cls, stt_factory, ["What is the difference between a process and a thread?"], script)
    assert _asked(ai_cls) == ["What is the difference between a process and a thread?"]


def test_untrusted_queue_fields_are_bounded() -> None:
    from app.api.websocket import MAX_CLIP_QUEUE_WAIT_SECONDS, _client_clip_no, _queued_seconds

    assert _client_clip_no(3) == 3
    for bad in (None, -1, True, "3", 2.5):
        assert _client_clip_no(bad) is None
    assert _queued_seconds(1500) == 1.5
    assert _queued_seconds(10**9) == MAX_CLIP_QUEUE_WAIT_SECONDS
    for bad in (None, -50, True, "100", float("nan")):
        assert _queued_seconds(bad) == 0.0


# ----------------------------------------------- 3. billing errors fail fast


class _Ctx:
    def __init__(self, handler) -> None:
        self.transport = httpx.MockTransport(handler)

    async def __aenter__(self):
        self.client = httpx.AsyncClient(transport=self.transport)
        return self.client

    async def __aexit__(self, *exc):
        await self.client.aclose()


@pytest.mark.asyncio
async def test_out_of_credits_429_is_not_retried_and_says_billing() -> None:
    from app.providers.stt import provider as stt_mod

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"error": {
            "message": "You have no credits remaining.", "type": "insufficient_quota",
            "code": "credit_balance_exhausted"}})

    p = stt_mod.GroqWhisperSTTProvider(api_key="k", base_url="https://stt.test/v1", model="m")
    with patch.object(stt_mod, "shared_http", lambda: _Ctx(handler)):
        with pytest.raises(stt_mod.STTRequestError) as err:
            await p.transcribe(b"RIFF....WAVEdata", mime_type="audio/wav")
    assert calls == 1
    assert str(err.value) == stt_mod.STT_BILLING_MESSAGE


@pytest.mark.asyncio
async def test_plain_rate_limit_is_still_retried() -> None:
    from app.providers.stt import provider as stt_mod

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, text="rate limited", headers={"Retry-After": "0"})
        return httpx.Response(200, json={"text": "What is a closure?"})

    p = stt_mod.GroqWhisperSTTProvider(api_key="k", base_url="https://stt.test/v1", model="m")
    with patch.object(stt_mod, "shared_http", lambda: _Ctx(handler)):
        assert await p.transcribe(b"RIFF....WAVEdata", mime_type="audio/wav") == "What is a closure?"
    assert calls == 2


@pytest.mark.asyncio
async def test_groq_rate_limit_with_billing_upgrade_link_is_still_retried() -> None:
    """Groq's 429 body links its billing page; it is a rate limit, not billing."""
    from app.providers.stt import provider as stt_mod

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {
                "message": "Rate limit reached for model `whisper-large-v3-turbo` in organization "
                           "`org_x` on requests per minute (RPM): Limit 20, Used 20, Requested 1. "
                           "Please try again in 2.1s. Need more tokens? Upgrade to Dev Tier today "
                           "at https://console.groq.com/settings/billing",
                "type": "requests", "code": "rate_limit_exceeded"}})
        return httpx.Response(200, json={"text": "What is a closure?"})

    p = stt_mod.GroqWhisperSTTProvider(api_key="k", base_url="https://stt.test/v1", model="m")
    with patch.object(stt_mod, "shared_http", lambda: _Ctx(handler)):
        assert await p.transcribe(b"RIFF....WAVEdata", mime_type="audio/wav") == "What is a closure?"
    assert calls == 2


@pytest.mark.asyncio
async def test_gemini_billing_403_is_not_reported_as_a_bad_key() -> None:
    from app.providers.stt import provider as stt_mod

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": 403, "status": "PERMISSION_DENIED",
            "message": "Your billing account has an outstanding balance. Make a payment ..."}})

    p = stt_mod.GeminiTranscribeSTTProvider(api_key="k", model="gemini-3.5-transcribe")
    with patch.object(stt_mod, "shared_http", lambda: _Ctx(handler)):
        with pytest.raises(stt_mod.STTRequestError) as err:
            await p.transcribe(b"RIFF....WAVEdata", mime_type="audio/wav")
    assert str(err.value) == stt_mod.STT_BILLING_MESSAGE


@pytest.mark.asyncio
async def test_gemini_real_bad_key_still_says_check_the_key() -> None:
    from app.providers.stt import provider as stt_mod

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": 403, "message": "API key not valid."}})

    p = stt_mod.GeminiTranscribeSTTProvider(api_key="k", model="gemini-3.5-transcribe")
    with patch.object(stt_mod, "shared_http", lambda: _Ctx(handler)):
        with pytest.raises(stt_mod.STTRequestError, match="GEMINI_API_KEY"):
            await p.transcribe(b"RIFF....WAVEdata", mime_type="audio/wav")


# ---------------------------------- 4. held half lost to a wh-word continuation
# Replay 2026-11-01 (DeepInfra, today's settings): "Walk me through what
# happens," + 1.2 s pause + "when you type a URL into the browser". The pause
# outlasted the resume check, so the first clip became a plain partial hold;
# the continuation opens with "when" (a question word), was taken for a NEW
# question, and the held half was dropped — only the second half was answered.


@pytest.mark.parametrize(
    "first, rest",
    [
        ("Walk me through what happens,", "when you type a URL into the browser"),
        ("Walk me through what happens,", "When you type a URL into the browser,"),
        ("Can you explain the difference between", "how a process and a thread share memory"),
        ("Explain how garbage collection works,", "where the young generation fits in."),
    ],
)
def test_held_half_is_stitched_to_a_wh_word_continuation(first, rest) -> None:
    b = QuestionBuffer()
    assert b.push_complete(first, now=0.0) is None and b.holding_partial
    out = b.push_complete(rest, now=1.5)
    stitched = f"{first} {rest}"
    assert out == stitched or (out is None and b.held_text == stitched)


def test_capitalised_fresh_question_still_replaces_an_abandoned_fragment() -> None:
    b = QuestionBuffer()
    assert b.push_complete("Can you explain the difference between", now=0.0) is None
    assert b.push_complete("What is a closure?", now=1.5) == "What is a closure?"
    assert not b.holding_partial


# ------------------------------------ 5. clip results applied in cut order
# Replay 2026-11-01 (DeepInfra degraded, 5-20 s per request): a clip stuck
# past the browser's 10 s slot timeout let the NEXT clip upload, and both
# transcribed at once. The later (fast) clip finished first and was stitched
# onto the held half of the EARLIER question:
# "…an interface How would you design a ratelimeter? and an abstract class…".


def _stt_with_delays(mapping: dict[bytes, tuple[float, str]]):
    import asyncio
    from unittest.mock import AsyncMock

    async def _transcribe(clip, mime_type=None):
        for suffix, (delay, text) in mapping.items():
            if clip.endswith(suffix):
                await asyncio.sleep(delay)
                return text
        raise AssertionError(f"unexpected clip {clip!r}")

    return type("FakeSTT", (), {"transcribe": AsyncMock(side_effect=_transcribe)})()


@patch("app.api.websocket.TURN_END_GRACE_SECONDS", 0.2)
@patch("app.api.websocket.TURN_QUIET_SECONDS", 0.3)
@patch("app.api.websocket.get_stt_provider")
@patch("app.api.websocket.AIService")
def test_a_slow_clip_is_never_overtaken_by_the_next_one(ai_cls, stt_factory) -> None:
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.test_interviewer_audio import _fake_guidance

    stt_factory.return_value = _stt_with_delays({
        b"q2-a": (0.0, "Explain the difference between an interface"),
        b"q2-b": (3.0, "and an abstract class in Java."),       # provider spike
        b"q3-question": (0.0, "How would you design a rate limiter?"),
    })
    ai_cls.return_value.analyze = AsyncMock(return_value=_fake_guidance())
    ai_cls.return_value.last_warning = None
    with patch("app.main.app"), TestClient(app) as client:
        from tests.test_interviewer_audio import _auth as _auth_ws

        _auth_ws(client)
        with client.websocket_connect("/ws/interview") as ws:
            ws.receive_json()
            ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/wav"})
            ws.send_json({"type": "speech_start", "clip": 1})
            _clip_n(ws, b"q2-a", 1)
            ws.send_json({"type": "speech_start", "clip": 2})  # kept talking
            _pinging_until(ws, lambda m: "rest of the question" in str(m.get("message", "")))
            _clip_n(ws, b"q2-b", 2)  # Q2 ends; its STT is stuck in a spike
            time.sleep(2.0)  # next question starts well after Q2 (not a resume)
            ws.send_json({"type": "speech_start", "clip": 3})
            # The browser's slot timed out on the slow clip: the next one goes too.
            _clip_n(ws, b"q3-question", 3)
            frames = _pinging_until(ws, lambda m: m.get("type") == "ai_guidance")
            frames += _pinging_until(ws, lambda m: m.get("type") == "ai_guidance")
    finals = [f["text"] for f in frames if f.get("type") == "stt_transcript" and f.get("final")]
    assert finals == ["and an abstract class in Java.", "How would you design a rate limiter?"]
    assert _asked(ai_cls) == [
        "Explain the difference between an interface and an abstract class in Java.",
        "How would you design a rate limiter?",
    ]


def _pinging_until(ws, pred, limit: int = 60) -> list[dict]:
    """Like _until, but pings before EVERY read, so a frame that never comes
    fails the test (limit reached) instead of blocking it forever."""
    frames = []
    for _ in range(limit):
        ws.send_json({"type": "ping"})
        m = ws.receive_json()
        frames.append(m)
        if pred(m):
            return frames
        if m.get("type") == "pong":
            time.sleep(0.1)
    raise AssertionError(f"condition never met: {[f for f in frames if f.get('type') != 'pong']}")


def _clip_n(ws, body: bytes, n: int) -> None:
    ws.send_bytes(b"\x1a\x45\xdf\xa3" + body)
    ws.send_json({"type": "audio_flush", "clip": n, "cutAgoMs": 0})


@patch("app.api.websocket.CLIP_ORDER_WAIT_SECONDS", 0.5)
@patch("app.api.websocket.get_stt_provider")
@patch("app.api.websocket.AIService")
def test_a_stuck_clip_delays_the_next_one_only_up_to_the_cap(ai_cls, stt_factory) -> None:
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.test_interviewer_audio import _auth as _auth_ws
    from tests.test_interviewer_audio import _fake_guidance

    # 4x the cap. It finishes before the socket closes: TestClient's teardown
    # randomly errors on a still-running call (pre-existing, any code version).
    stt_factory.return_value = _stt_with_delays({
        b"stuck-clip": (2.0, "Okay."),
        b"next-question": (0.0, "What is a closure?"),
    })
    ai_cls.return_value.analyze = AsyncMock(return_value=_fake_guidance())
    ai_cls.return_value.last_warning = None
    with patch("app.main.app"), TestClient(app) as client:
        _auth_ws(client)
        with client.websocket_connect("/ws/interview") as ws:
            ws.receive_json()
            ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/wav"})
            _clip_n(ws, b"stuck-clip", 1)
            t0 = time.monotonic()
            _clip_n(ws, b"next-question", 2)
            frames = _pinging_until(ws, lambda m: m.get("type") == "ai_guidance")
            waited = time.monotonic() - t0
            # Let the stuck clip finish before closing (see above).
            frames += _pinging_until(ws, lambda m: m.get("text") == "Okay.")
    assert any(f.get("text") == "What is a closure?" for f in frames)
    assert 0.4 <= waited < 1.8, waited  # the cap (0.5 s), not the stuck call (2 s)
    assert _asked(ai_cls) == ["What is a closure?"]
