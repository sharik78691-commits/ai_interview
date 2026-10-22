"""Streaming interviewer transcript (word-by-word, like a voice assistant).

The clip pipeline (silence cut -> batch STT -> question detection) only knows
the text once a sentence has ENDED. This module keeps ONE Gemini Live session
open while the interviewer audio is captured and streams 16 kHz PCM into it as
it arrives; the Live API answers with `interimInputTranscription` every ~0.5s
(measured 2026-10-20: first word ~0.5s after onset, ~0.5s behind speech) and a
`inputTranscription` when its own VAD closes the turn.

The text is relayed to the browser as `stt_live` frames. This module never
answers anything itself: the WebSocket's LiveTurnTracker decides when a turn
holds a finished question (never on an interim word while the interviewer is
still talking), and the clip pipeline stays the fallback for speech this
stream did not hear.

Design constraints:
- `feed()` never blocks the WebSocket receive loop: frames go into a bounded
  queue drained by a background sender (connect/TLS happens there).
- The session opens lazily on the first frame and closes after IDLE_CLOSE_S
  without audio (the client only streams while someone is talking), so a
  quiet meeting costs nothing; the next frame reopens it.
- A failing provider backs off for RETRY_AFTER_S instead of reconnecting on
  every frame.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

#: Every streamed frame starts with this tag so it can share the socket with
#: the clip pipeline's WAV/WebM frames (RIFF / 0x1A45DFA3) without ambiguity.
LIVE_PCM_TAG = b"LPCM"
#: The client always resamples to this rate (16-bit mono little-endian).
LIVE_PCM_RATE = 16_000
#: ~5 s of 85 ms frames; beyond that the oldest audio is dropped (a stalled
#: provider must not grow memory — stale words are worthless for live text).
MAX_QUEUED_FRAMES = 60
IDLE_CLOSE_S = 20.0
RETRY_AFTER_S = 10.0
CONNECT_TIMEOUT_S = 8.0

OnText = Callable[[str, bool], Awaitable[None]]


def _connect(api_key: str):
    """Open the Live socket (patched in tests). Key in a header, never the URL."""
    from websockets.asyncio.client import connect

    from app.providers.stt.provider import GEMINI_LIVE_URL

    return connect(
        GEMINI_LIVE_URL,
        additional_headers={"x-goog-api-key": api_key},
        max_size=None,
        open_timeout=CONNECT_TIMEOUT_S,
    )


class LiveTranscriptStream:
    """One per interview WebSocket. Call feed() per PCM frame, close() at the end."""

    def __init__(self, api_key: str, model: str, on_text: OnText) -> None:
        self.api_key = api_key
        self.model = model
        self.on_text = on_text
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=MAX_QUEUED_FRAMES)
        self._pump: asyncio.Task | None = None
        self._closed = False
        self._paused_until = 0.0

    @property
    def available(self) -> bool:
        """False while closed or backing off after a failure (hears nothing)."""
        return not self._closed and time.monotonic() >= self._paused_until

    def feed(self, pcm: bytes) -> None:
        """Queue one PCM frame; never awaits (safe inside the receive loop)."""
        if self._closed or not pcm or time.monotonic() < self._paused_until:
            return
        if self._queue.full():
            try:
                self._queue.get_nowait()  # newest audio matters most
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(pcm)
        if self._pump is None or self._pump.done():
            self._pump = asyncio.create_task(self._run())

    def warm(self) -> None:
        """Open the session ahead of speech (capture start) so the FIRST word
        does not pay the connect + TLS + setup round-trips (~0.6s measured).
        Released again after IDLE_CLOSE_S if nobody speaks."""
        if self._closed or time.monotonic() < self._paused_until:
            return
        if self._pump is None or self._pump.done():
            self._pump = asyncio.create_task(self._run())

    async def close(self) -> None:
        self._closed = True
        if self._pump and not self._pump.done():
            self._pump.cancel()
            await asyncio.gather(self._pump, return_exceptions=True)

    async def _run(self) -> None:
        """Hold one Live session while audio keeps arriving."""
        try:
            async with _connect(self.api_key) as ws:
                await ws.send(
                    json.dumps(
                        {"setup": {"model": f"models/{self.model}", "inputAudioTranscription": {}}}
                    )
                )
                first = json.loads(await ws.recv())
                if "setupComplete" not in first:
                    raise RuntimeError("Live API setup was not accepted")
                logger.info("Live transcript session opened (model=%s)", self.model)
                reader = asyncio.create_task(self._read(ws))
                try:
                    await self._send_loop(ws, reader)
                finally:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
            logger.info("Live transcript session closed (idle)")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Display-only feature: log, back off, never surface as an error.
            self._paused_until = time.monotonic() + RETRY_AFTER_S
            detail = str(getattr(getattr(exc, "rcvd", None), "reason", "") or exc)
            logger.warning(
                "Live transcript unavailable for %.0fs: %s %s",
                RETRY_AFTER_S,
                type(exc).__name__,
                detail[:160].replace(self.api_key, "[redacted]") if self.api_key else detail[:160],
            )
            # Audio queued for the dead session is stale by the time we retry.
            while not self._queue.empty():
                self._queue.get_nowait()

    async def _send_loop(self, ws, reader: asyncio.Task) -> None:
        mime = f"audio/pcm;rate={LIVE_PCM_RATE}"
        while True:
            get = asyncio.ensure_future(self._queue.get())
            done, _ = await asyncio.wait(
                {get, reader}, timeout=IDLE_CLOSE_S, return_when=asyncio.FIRST_COMPLETED
            )
            if get not in done:
                get.cancel()
                if reader in done:
                    # Server ended the session (e.g. its length limit): a clean
                    # close returns so the next frame reopens at once; an
                    # error propagates and triggers the back-off.
                    reader.result()
                return  # or nobody is talking: release the session
            pcm = get.result()
            await ws.send(
                json.dumps(
                    {"realtimeInput": {"audio": {"data": base64.b64encode(pcm).decode(), "mimeType": mime}}}
                )
            )

    async def _read(self, ws) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            content = msg.get("serverContent") or {}
            final = (content.get("inputTranscription") or {}).get("text")
            if final:
                await self.on_text(str(final).strip(), True)
                continue
            interim = (content.get("interimInputTranscription") or {}).get("text")
            if interim:
                await self.on_text(str(interim).strip(), False)
