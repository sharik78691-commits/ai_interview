"""Live transcript vs. the batch-STT latency tail (measured locally 2026-10-29).

Driving the local backend like the browser (10.4 s question, Whisper on
DeepInfra) showed snapshots normally taking 0.6–1.0 s, but one in ~10 taking
3–4 s. With one preview at a time that froze the Transcript line; the slow
result then landed after the clip cut and was DROPPED as stale, so the last
words appeared all at once with the final — which itself took up to 7.6 s.

Pinned:
1. a snapshot stuck past PREVIEW_HEDGE_SECONDS gets a newer one alongside; the
   newer text is shown and the slow, older result never overwrites it;
2. a preview of the clip that was just cut is still shown while its final is
   pending — and never after the final;
3. the final STT call is hedged: a second request after its normal time, first
   success wins, the loser is cancelled; failures surface as before.
"""
import asyncio
import time
from unittest.mock import patch

import pytest

from app.api import websocket as ws_mod
from tests.test_stt_live_preview import WEBM, _auth, _is_pong, _recv_until


class _ScriptedSTT:
    """transcribe() answers from a script of (delay_s, text) per call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.cancelled = 0

    async def transcribe(self, clip, mime_type=None):
        delay, text = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if isinstance(text, Exception):
            raise text
        return text


def _session(stt, script):
    from fastapi.testclient import TestClient

    from app.main import app

    with patch("app.api.websocket.get_stt_provider", return_value=stt), \
            patch("app.main.app"), TestClient(app) as client:
        _auth(client)
        with client.websocket_connect("/ws/interview") as ws:
            ws.receive_json()  # handshake
            ws.send_json({"type": "interviewer_audio_start"})
            return script(ws)


def _previews(frames):
    return [m["text"] for m in frames if m.get("type") == "stt_transcript" and m.get("final") is False]


@patch("app.api.websocket.PREVIEW_HEDGE_SECONDS", 0.2)
def test_slow_snapshot_does_not_freeze_the_line() -> None:
    stt = _ScriptedSTT([(1.5, "Can you"), (0.05, "Can you explain the difference")])

    def script(ws):
        ws.send_bytes(WEBM + b"snap-1")
        ws.send_json({"type": "audio_preview"})
        time.sleep(0.3)  # snap-1 is now "slow"
        ws.send_bytes(WEBM + b"snap-2")
        ws.send_json({"type": "audio_preview"})
        frames = _recv_until(ws, lambda m: m.get("type") == "stt_transcript")
        time.sleep(1.5)  # let the slow, older result land
        ws.send_json({"type": "ping"})
        return frames + _recv_until(ws, _is_pong)

    frames = _session(stt, script)
    assert _previews(frames) == ["Can you explain the difference"]  # newer shown, older never overwrites
    assert stt.calls == 2


def test_preview_of_the_just_cut_clip_shows_until_its_final() -> None:
    # preview 0.3 s, final 1.0 s: the preview lands after the cut, before the final.
    stt = _ScriptedSTT([(0.3, "Explain the Java collection"), (1.0, "Explain the Java collection framework.")])

    def script(ws):
        ws.send_bytes(WEBM + b"snap")
        ws.send_json({"type": "audio_preview"})
        ws.send_bytes(WEBM + b"clip")
        ws.send_json({"type": "audio_flush"})
        return _recv_until(ws, lambda m: m.get("type") == "stt_transcript" and m.get("final") is True)

    frames = _session(stt, script)
    kinds = [(m.get("final"), m["text"]) for m in frames if m.get("type") == "stt_transcript"]
    assert kinds == [(False, "Explain the Java collection"), (True, "Explain the Java collection framework.")]


def test_preview_landing_after_its_final_is_never_shown() -> None:
    stt = _ScriptedSTT([(1.0, "Explain the Java"), (0.05, "Explain the Java collection framework.")])

    def script(ws):
        ws.send_bytes(WEBM + b"snap")
        ws.send_json({"type": "audio_preview"})
        ws.send_bytes(WEBM + b"clip")
        ws.send_json({"type": "audio_flush"})
        frames = _recv_until(ws, lambda m: m.get("type") == "stt_transcript" and m.get("final") is True)
        time.sleep(1.2)
        ws.send_json({"type": "ping"})
        return frames + _recv_until(ws, _is_pong)

    assert _previews(_session(stt, script)) == []


# ------------------------------------------------------------- hedged final


async def test_slow_final_is_hedged_and_the_loser_cancelled() -> None:
    stt = _ScriptedSTT([(5.0, "slow"), (0.05, "fast")])
    with patch.object(ws_mod, "FINAL_HEDGE_BASE_SECONDS", 0.1), patch.object(ws_mod, "FINAL_HEDGE_PER_AUDIO_SECOND", 0.0):
        t0 = time.monotonic()
        text = await ws_mod._transcribe_hedged(stt, b"RIFF" + b"\x00" * 60, "audio/wav")
    assert text == "fast" and time.monotonic() - t0 < 1.0
    await asyncio.sleep(0)
    assert stt.cancelled == 1


async def test_fast_final_sends_one_request_only() -> None:
    stt = _ScriptedSTT([(0.01, "Explain the Java collection framework.")])
    assert await ws_mod._transcribe_hedged(stt, b"x", "audio/wav") == "Explain the Java collection framework."
    assert stt.calls == 1


async def test_hedged_final_failures_surface_like_before() -> None:
    from app.providers.stt.provider import STTRequestError

    early = _ScriptedSTT([(0.01, STTRequestError("bad audio"))])
    with pytest.raises(STTRequestError):
        await ws_mod._transcribe_hedged(early, b"x", "audio/wav")
    assert early.calls == 1  # no hedge for a failure before the hedge point

    both = _ScriptedSTT([(0.3, STTRequestError("first")), (0.3, STTRequestError("second"))])
    with patch.object(ws_mod, "FINAL_HEDGE_BASE_SECONDS", 0.1), patch.object(ws_mod, "FINAL_HEDGE_PER_AUDIO_SECOND", 0.0):
        with pytest.raises(STTRequestError, match="first"):
            await ws_mod._transcribe_hedged(both, b"x", "audio/wav")
