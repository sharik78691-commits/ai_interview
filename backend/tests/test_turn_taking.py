"""Half-asked questions must not be answered (clip pipeline turn-taking).

Bug (2026-10-29): with a batch STT (Whisper on DeepInfra) answers come from
clips cut after ~0.85 s of quiet. People pause that long mid-question, and the
STT punctuates every clip, so "Explain the Java collection framework." was
answered while the interviewer went on "…hierarchy and where HashMap fits".

Pinned:
1. a clip whose speaker started talking again within the resume window is
   held and stitched onto the next clip — ONE answer for the whole question;
2. with no resumed speech the clip is answered as before (no extra wait
   beyond the grace that batch STT already covers);
3. a false start (cough: speech_start, then no clip) is answered once the
   speaker is quiet — never lost, never twice;
4. speech that starts after the resume window is a NEW question;
5. QuestionBuffer: a continuing hold stitches whatever the next clip opens
   with, ignores backchannels, never expires on HOLD_MS, is released once.
"""
import time
from unittest.mock import AsyncMock, patch

from app.services.question_service import QuestionBuffer
from tests.test_interviewer_audio import _auth, _fake_guidance

WEBM = b"\x1a\x45\xdf\xa3"


# ------------------------------------------------------------ QuestionBuffer


def test_continuing_hold_stitches_even_a_question_opener_and_answers_once() -> None:
    b = QuestionBuffer()
    b.hold_continuing("Explain the Java collection framework.", now=0.0)
    assert b.holding_continuing
    # Would normally start a NEW question; the speaker never stopped, so it is the same turn.
    out = b.push_complete("What about the hierarchy and where HashMap fits in?", now=1.0)
    assert out == "Explain the Java collection framework. What about the hierarchy and where HashMap fits in?"
    assert not b.holding_partial and b.take_continuing() is None


def test_continuing_hold_survives_backchannel_and_hold_deadline() -> None:
    b = QuestionBuffer()
    b.hold_continuing("Explain the Java collection framework.", now=0.0)
    assert b.push_complete("okay", now=1.0) is None  # backchannel: still waiting
    assert b.flush_expired(now=60.0) is None  # never released by the HOLD_MS timer
    late = b.push_complete("and its hierarchy?", now=30.0)  # long continuation, not expired
    assert late == "Explain the Java collection framework. and its hierarchy?"


def test_take_continuing_releases_exactly_once() -> None:
    b = QuestionBuffer()
    b.hold_continuing("Explain the Java collection framework.", now=0.0)
    assert b.take_continuing() == "Explain the Java collection framework."
    assert b.take_continuing() is None
    assert b.push_complete("What is a closure?", now=1.0) == "What is a closure?"  # judged alone


# ----------------------------------------------------------------- WebSocket


def _run(ai_cls, stt_factory, texts, script) -> list[dict]:
    from fastapi.testclient import TestClient

    from app.main import app

    stt_factory.return_value = type("FakeSTT", (), {"transcribe": AsyncMock(side_effect=list(texts))})()
    ai_cls.return_value.analyze = AsyncMock(return_value=_fake_guidance())
    ai_cls.return_value.last_warning = None
    with patch("app.main.app"), TestClient(app) as client:
        _auth(client)
        with client.websocket_connect("/ws/interview") as ws:
            hello = ws.receive_json()
            assert hello["turnSignals"] is True
            ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/wav"})
            frames = script(ws)
            # Drain: a second answer would show up here.
            ws.send_json({"type": "ping"})
            frames += _until(ws, lambda m: m.get("type") == "pong")
    return frames


def _until(ws, pred, limit: int = 40) -> list[dict]:
    """Read frames until ``pred``; pings keep a missing frame from hanging."""
    frames = []
    for i in range(limit):
        if i % 3 == 2:
            ws.send_json({"type": "ping"})
        m = ws.receive_json()
        frames.append(m)
        if pred(m):
            return frames
    raise AssertionError(f"condition never met: {frames}")


def _clip(ws, body: bytes) -> None:
    ws.send_bytes(WEBM + body)
    ws.send_json({"type": "audio_flush"})


def _asked(ai_cls) -> list[str]:
    return [c.args[0] for c in ai_cls.return_value.analyze.await_args_list]


@patch("app.api.websocket.TURN_END_GRACE_SECONDS", 0.3)
@patch("app.api.websocket.get_stt_provider")
@patch("app.api.websocket.AIService")
def test_resumed_speech_holds_the_clip_and_answers_the_whole_question_once(ai_cls, stt_factory) -> None:
    def script(ws):
        _clip(ws, b"first-half")
        ws.send_json({"type": "speech_start"})  # talking again right after the cut
        frames = _until(ws, lambda m: "rest of the question" in str(m.get("message", "")))
        _clip(ws, b"second-half")
        return frames + _until(ws, lambda m: m.get("type") == "ai_guidance")

    _run(ai_cls, stt_factory, ["Explain the Java collection framework.",
                               "Hierarchy and where HashMap fits in?"], script)
    assert _asked(ai_cls) == ["Explain the Java collection framework. Hierarchy and where HashMap fits in?"]


@patch("app.api.websocket.get_stt_provider")
@patch("app.api.websocket.AIService")
def test_finished_question_without_resumed_speech_is_answered_promptly(ai_cls, stt_factory) -> None:

    def script(ws):
        _clip(ws, b"whole")
        return _until(ws, lambda m: m.get("type") == "ai_guidance")

    _run(ai_cls, stt_factory, ["Explain the Java collection framework hierarchy."], script)
    assert _asked(ai_cls) == ["Explain the Java collection framework hierarchy."]


@patch("app.api.websocket.TURN_END_GRACE_SECONDS", 0.3)
@patch("app.api.websocket.TURN_QUIET_SECONDS", 0.3)
@patch("app.api.websocket.get_stt_provider")
@patch("app.api.websocket.AIService")
def test_false_start_is_answered_once_the_speaker_is_quiet(ai_cls, stt_factory) -> None:
    def script(ws):
        _clip(ws, b"question")
        ws.send_json({"type": "speech_start"})
        ws.send_json({"type": "interviewer_audio_stop"})  # the "speech" never became a clip
        return _until(ws, lambda m: m.get("type") == "ai_guidance")

    _run(ai_cls, stt_factory, ["Explain the Java collection framework hierarchy."], script)
    assert _asked(ai_cls) == ["Explain the Java collection framework hierarchy."]


@patch("app.api.websocket.TURN_END_GRACE_SECONDS", 0.4)
@patch("app.api.websocket.TURN_RESUME_WINDOW_SECONDS", 0.05)
@patch("app.api.websocket.VOICE_SIGNAL_LAG_SECONDS", 0.0)
@patch("app.api.websocket.get_stt_provider")
@patch("app.api.websocket.AIService")
def test_speech_after_the_resume_window_is_a_new_question(ai_cls, stt_factory) -> None:
    def script(ws):
        _clip(ws, b"question")
        time.sleep(0.2)  # a real gap: the next utterance is a new question
        ws.send_json({"type": "speech_start"})
        return _until(ws, lambda m: m.get("type") == "ai_guidance")

    _run(ai_cls, stt_factory, ["Explain the Java collection framework hierarchy."], script)
    assert _asked(ai_cls) == ["Explain the Java collection framework hierarchy."]


# ---------------------------------------------- stitched, finished, no "?"
#
# Seen locally 2026-10-29: "…Explain method overloading vs method overriding.
# What are checked and unchecked exceptions" (stitched onto a held half, and
# returned by the STT without a closing "?") waited the full 5 s HOLD_MS
# although the interviewer had stopped talking.


def test_take_held_releases_only_the_expected_plain_hold() -> None:
    b = QuestionBuffer()
    assert b.push_complete("Can you explain the difference between", now=0.0) is None
    assert b.push_complete("method overloading and method overriding", now=1.0) is None  # no "?"
    assert b.take_held("something else") is None
    held = "Can you explain the difference between method overloading and method overriding"
    assert b.take_held(held) == held
    assert b.take_held(held) is None and not b.holding_partial  # exactly once


@patch("app.api.websocket.TURN_END_GRACE_SECONDS", 0.05)
@patch("app.api.websocket.TURN_QUIET_SECONDS", 0.3)
@patch("app.api.websocket.get_stt_provider")
@patch("app.api.websocket.AIService")
def test_finished_but_unpunctuated_stitch_answers_when_quiet_not_after_5s(ai_cls, stt_factory) -> None:
    timing = {}

    def script(ws):
        _clip(ws, b"half")
        frames = _until(ws, lambda m: m.get("type") == "stt_transcript")
        timing["t0"] = time.monotonic()
        _clip(ws, b"rest")
        frames += _until(ws, lambda m: m.get("type") == "ai_guidance")
        timing["answered"] = time.monotonic() - timing["t0"]
        return frames

    _run(ai_cls, stt_factory, ["Can you explain the difference between",
                               "method overloading and method overriding"], script)
    assert _asked(ai_cls) == ["Can you explain the difference between method overloading and method overriding"]
    assert timing["answered"] < 2.5  # was 5 s (HOLD_MS)


@patch("app.api.websocket.TURN_END_GRACE_SECONDS", 0.05)
@patch("app.api.websocket.TURN_QUIET_SECONDS", 0.3)
@patch("app.api.websocket.get_stt_provider")
@patch("app.api.websocket.AIService")
def test_three_clip_question_still_waits_while_the_interviewer_talks(ai_cls, stt_factory) -> None:
    def script(ws):
        _clip(ws, b"clip-one")
        frames = _until(ws, lambda m: m.get("type") == "stt_transcript")
        ws.send_json({"type": "speech_start"})  # still talking: clip three is coming
        _clip(ws, b"clip-two")  # its flush ends "speaking"; announce the next clip again
        ws.send_json({"type": "speech_start"})
        frames += _until(ws, lambda m: m.get("type") == "stt_transcript")
        time.sleep(0.8)  # well past TURN_QUIET_SECONDS: must NOT answer while speaking
        _clip(ws, b"clip-three")
        return frames + _until(ws, lambda m: m.get("type") == "ai_guidance")

    _run(ai_cls, stt_factory, ["Can you walk me through how you would",
                               "design a rate limiter for a gateway",
                               "that handles a million requests per second?"], script)
    assert _asked(ai_cls) == ["Can you walk me through how you would design a rate limiter for a gateway "
                              "that handles a million requests per second?"]


# ------------------------------------------------ clips cut by the 15 s cap


def _wav(seconds: float) -> bytes:
    """A real WAV header (16 kHz mono 16-bit) with `seconds` of silence."""
    import struct

    data = b"\x00\x00" * int(16_000 * seconds)
    return (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16_000, 32_000, 2, 16)
            + b"data" + struct.pack("<I", len(data)) + data)


@patch("app.api.websocket.TURN_END_GRACE_SECONDS", 0.3)
@patch("app.api.websocket.get_stt_provider")
@patch("app.api.websocket.AIService")
def test_clip_cut_by_the_15s_cap_is_answered_even_though_speech_continues(ai_cls, stt_factory) -> None:
    """Seen locally 2026-10-29: a long monologue was cut by the 15 s cap three
    times; every cut "looked" resumed, so all were held and answered as one
    blob ~50 s later. A cap cut is not a pause: judge the clip on its text."""
    def script(ws):
        ws.send_bytes(_wav(14.8))
        ws.send_json({"type": "audio_flush", "mimeType": "audio/wav"})
        ws.send_json({"type": "speech_start"})  # they just keep talking
        return _until(ws, lambda m: m.get("type") == "ai_guidance")

    q = "yeah hi so let me start with that question so what is the difference between hashmap and hashtable"
    _run(ai_cls, stt_factory, [q], script)
    assert _asked(ai_cls) == [q]


@patch("app.api.websocket.TURN_END_GRACE_SECONDS", 0.3)
@patch("app.api.websocket.TURN_QUIET_SECONDS", 0.3)
@patch("app.api.websocket.get_stt_provider")
@patch("app.api.websocket.AIService")
def test_short_clip_with_resumed_speech_is_still_held(ai_cls, stt_factory) -> None:
    def script(ws):
        ws.send_bytes(_wav(3.0))
        ws.send_json({"type": "audio_flush", "mimeType": "audio/wav"})
        ws.send_json({"type": "speech_start"})
        return _until(ws, lambda m: "rest of the question" in str(m.get("message", "")))

    _run(ai_cls, stt_factory, ["Explain the Java collection framework."], script)
