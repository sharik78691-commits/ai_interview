"""Word-by-word live transcript stream (Gemini Live) — mocked socket, no network.

Before: the Transcript line updated only from batch snapshots (~1.5–2.5s
jumps, and a Groq 429 froze it for 20s). Now interviewer PCM is streamed into
one Live session and interim text is relayed as `stt_live` frames.

Pinned:
1. frames are streamed (setup once, then realtimeInput audio) and interim /
   final text is relayed in order;
2. the session closes when nobody talks and reopens on the next frame;
3. a provider failure backs off silently instead of reconnecting per frame;
4. in the WebSocket, LPCM frames never leak into the answer clip and INTERIM
   live text never triggers an answer (finished turns: test_live_answers.py);
5. the handshake only advertises liveStream when a Gemini key exists.
"""
import asyncio
import base64
import concurrent.futures
import contextlib
import json
from unittest.mock import AsyncMock, patch

from app.providers.stt import live_stream as ls


class FakeLiveSocket:
    """Records frames; replays scripted server messages once audio arrives."""

    def __init__(self, replies: list[dict] | None = None, fail: Exception | None = None):
        self.replies = list(replies or [])
        self.fail = fail
        self.sent: list[dict] = []
        self._inbox: asyncio.Queue = asyncio.Queue()
        self.closed = False

    async def __aenter__(self):
        if self.fail:
            raise self.fail
        return self

    async def __aexit__(self, *a):
        self.closed = True
        await self._inbox.put(None)
        return False

    async def send(self, data: str):
        msg = json.loads(data)
        self.sent.append(msg)
        if "realtimeInput" in msg and self.replies:
            await self._inbox.put(json.dumps(self.replies.pop(0)))

    async def recv(self):
        return json.dumps({"setupComplete": {}})

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._inbox.get()
        if item is None:
            raise StopAsyncIteration
        return item


def _interim(t: str) -> dict:
    return {"serverContent": {"interimInputTranscription": {"text": t}}}


def _final(t: str) -> dict:
    return {"serverContent": {"inputTranscription": {"text": t}}}


async def _settle(times: int = 20) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


class TestLiveTranscriptStream:
    async def test_streams_audio_and_relays_interim_then_final(self) -> None:
        sock = FakeLiveSocket([_interim("Can"), _interim("Can you explain"), _final("Can you explain?")])
        got: list[tuple[str, bool]] = []

        async def on_text(text, final):
            got.append((text, final))

        with patch.object(ls, "_connect", lambda key: sock):
            stream = ls.LiveTranscriptStream("k", "live-model", on_text)
            for i in range(3):
                stream.feed(b"\x01\x00" * 160 + bytes([i]))
                await _settle()
            await stream.close()

        assert sock.sent[0] == {"setup": {"model": "models/live-model", "inputAudioTranscription": {}}}
        audio = [m["realtimeInput"]["audio"] for m in sock.sent[1:]]
        assert len(audio) == 3
        assert all(a["mimeType"] == "audio/pcm;rate=16000" for a in audio)
        assert base64.b64decode(audio[2]["data"]).endswith(b"\x02")
        assert got == [("Can", False), ("Can you explain", False), ("Can you explain?", True)]

    async def test_idle_session_closes_and_next_frame_reopens(self) -> None:
        socks: list[FakeLiveSocket] = []

        def _conn(key):
            socks.append(FakeLiveSocket())
            return socks[-1]

        with patch.object(ls, "_connect", _conn), patch.object(ls, "IDLE_CLOSE_S", 0.05):
            stream = ls.LiveTranscriptStream("k", "m", AsyncMock())
            stream.feed(b"\x00\x00" * 10)
            await asyncio.sleep(0.15)  # nobody talks -> released
            assert socks[0].closed
            stream.feed(b"\x00\x00" * 10)
            await _settle()
            await stream.close()

        assert len(socks) == 2

    async def test_failure_backs_off_instead_of_reconnecting_per_frame(self) -> None:
        attempts = 0

        def _conn(key):
            nonlocal attempts
            attempts += 1
            return FakeLiveSocket(fail=OSError("network down"))

        on_text = AsyncMock()
        with patch.object(ls, "_connect", _conn):
            stream = ls.LiveTranscriptStream("k", "m", on_text)
            stream.feed(b"\x00\x00")
            await _settle()
            for _ in range(5):
                stream.feed(b"\x00\x00")
                await _settle()
            await stream.close()

        assert attempts == 1
        on_text.assert_not_awaited()

    async def test_warm_opens_the_session_before_speech(self) -> None:
        sock = FakeLiveSocket()
        with patch.object(ls, "_connect", lambda key: sock):
            stream = ls.LiveTranscriptStream("k", "m", AsyncMock())
            stream.warm()
            await _settle()
            # Setup already done; no audio sent yet.
            assert sock.sent == [{"setup": {"model": "models/m", "inputAudioTranscription": {}}}]
            stream.feed(b"\x00\x00")
            await _settle()
            await stream.close()
        assert len(sock.sent) == 2

    async def test_queue_is_bounded(self) -> None:
        with patch.object(ls, "_connect", lambda key: FakeLiveSocket(fail=OSError("x"))):
            stream = ls.LiveTranscriptStream("k", "m", AsyncMock())
            # Fill synchronously, before the pump task ever runs.
            while not stream._queue.full():
                stream._queue.put_nowait(b"old")
            stream.feed(b"newest")
            assert stream._queue.qsize() == ls.MAX_QUEUED_FRAMES
            await stream.close()


@contextlib.contextmanager
def ws_session(client):
    """``websocket_connect``, minus a TestClient teardown race.

    On exit Starlette cancels the handler's scope right after the close frame.
    If the handler is still awaiting its background tasks at that moment, the
    cancellation surfaces as ``concurrent.futures.CancelledError``. Only that
    error, and only after the test body finished, is tolerated — a failing
    body still fails.
    """
    body_done = False
    try:
        with client.websocket_connect("/ws/interview") as ws:
            yield ws
            body_done = True
    except concurrent.futures.CancelledError:
        if not body_done:
            raise


def _auth(client) -> None:
    client.post(
        "/api/auth/register",
        json={"email": "ws-live@example.com", "password": "Passw0rd123", "name": "WS"},
    )


def _recv_until(ws, pred, limit: int = 30) -> list[dict]:
    frames: list[dict] = []
    for i in range(limit):
        if i % 3 == 2:
            ws.send_json({"type": "ping"})
        msg = ws.receive_json()
        frames.append(msg)
        if pred(msg):
            return frames
    raise AssertionError(f"condition never met: {frames}")


class TestWebSocketLiveStream:
    def test_no_gemini_key_means_no_live_stream(self) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                hello = ws.receive_json()
        assert hello.get("liveStream") is False

    @patch("app.api.websocket.get_stt_provider")
    @patch("app.api.websocket.AIService")
    def test_live_frames_stream_text_and_never_touch_the_clip(self, ai_cls, stt_factory) -> None:
        from fastapi.testclient import TestClient

        from app.core.config import get_settings
        from app.main import app

        sock = FakeLiveSocket([_interim("Can you"), _interim("Can you explain")])
        stt_factory.return_value = type(
            "FakeSTT", (), {"transcribe": AsyncMock(return_value="Okay, thanks.")}
        )()
        ai_cls.return_value.analyze = AsyncMock()
        ai_cls.return_value.last_warning = None

        with patch.object(get_settings(), "gemini_api_key", "test-key"), patch.object(
            ls, "_connect", lambda key: sock
        ):
            with patch("app.main.app"), TestClient(app) as client:
                _auth(client)
                with ws_session(client) as ws:
                    hello = ws.receive_json()
                    assert hello.get("liveStream") is True
                    ws.send_json({"type": "interviewer_audio_start", "mimeType": "audio/wav"})
                    ws.send_bytes(ls.LIVE_PCM_TAG + b"\x01\x00" * 800)
                    ws.send_bytes(ls.LIVE_PCM_TAG + b"\x02\x00" * 800)
                    frames = _recv_until(
                        ws, lambda m: m.get("type") == "stt_live" and m["text"] == "Can you explain"
                    )
                    # A real clip arrives too: it must be exactly the clip bytes.
                    ws.send_bytes(b"\x1a\x45\xdf\xa3clip-bytes")
                    ws.send_json({"type": "audio_flush"})
                    frames += _recv_until(ws, lambda m: m.get("type") == "stt_transcript")
                    ws.send_json({"type": "ping"})
                    frames += _recv_until(ws, lambda m: m.get("type") == "pong")

        live = [m for m in frames if m.get("type") == "stt_live"]
        assert live == [
            {"type": "stt_live", "text": "Can you", "final": False},
            {"type": "stt_live", "text": "Can you explain", "final": False},
        ]
        clip = stt_factory.return_value.transcribe.await_args_list[0].args[0]
        assert clip == b"\x1a\x45\xdf\xa3clip-bytes"
        # Live text is display only: nothing was answered.
        ai_cls.return_value.analyze.assert_not_awaited()

    def test_live_frames_ignored_when_not_capturing(self) -> None:
        from fastapi.testclient import TestClient

        from app.core.config import get_settings
        from app.main import app

        opened: list[int] = []

        def _conn(key):
            opened.append(1)
            return FakeLiveSocket()

        with patch.object(get_settings(), "gemini_api_key", "test-key"), patch.object(
            ls, "_connect", _conn
        ):
            with patch("app.main.app"), TestClient(app) as client:
                _auth(client)
                with client.websocket_connect("/ws/interview") as ws:
                    ws.receive_json()
                    ws.send_bytes(ls.LIVE_PCM_TAG + b"\x01\x00" * 800)
                    ws.send_json({"type": "ping"})
                    _recv_until(ws, lambda m: m.get("type") == "pong")
        assert opened == []
