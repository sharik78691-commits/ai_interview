"""Concrete STT providers.

- GroqWhisperSTTProvider: real transcription (whisper-large-v3-turbo).
- MockSTTProvider: used when no STT credentials exist, so the UI still works
  and can explain that transcription is unavailable.
"""
import asyncio
import logging

import httpx

from app.core.config import get_settings
from app.providers.stt.base import STTProvider

logger = logging.getLogger(__name__)

# Transcription is called once per spoken question, so a burst of questions can
# hit the provider's rate limit. Retry a few times before giving up.
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.5, 3.5)


class STTUnavailableError(RuntimeError):
    """Raised when no speech-to-text backend is configured at all."""


class STTRequestError(RuntimeError):
    """Raised when a provider call fails (wrong model, network, quota...)."""


class GroqWhisperSTTProvider(STTProvider):
    """Whisper transcription via any OpenAI-compatible /audio/transcriptions."""

    name = "whisper"

    def __init__(self, api_key: str = "", base_url: str = "", model: str = "") -> None:
        settings = get_settings()
        self.api_key = api_key or settings.stt_api_key or settings.llm_api_key
        self.base_url = (base_url or settings.stt_base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.stt_model

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
    ) -> str:
        if not self.available:
            raise STTUnavailableError(
                "No speech-to-text credentials configured (set STT_API_KEY in backend/.env)."
            )
        if not audio_bytes:
            return ""

        data = {
            "model": self.model,
            "response_format": "json",
            "temperature": "0",
            "prompt": "Interviewer speech in a job interview. Transcribe verbatim.",
        }
        # Name the part after what the bytes actually are. Strict providers use
        # the filename/content-type to pick a decoder, so a mismatched label
        # (e.g. WAV bytes sent as clip.webm) is rejected as an invalid file.
        # mime_type is authoritative; `filename` stays for interface parity.
        send_name = self.filename_for(mime_type)
        files = {"file": (send_name, audio_bytes, mime_type)}
        logger.info(
            "STT request: %d bytes, mime=%s, part=%s, model=%s",
            len(audio_bytes),
            mime_type,
            send_name,
            self.model,
        )

        last_error: str = ""
        for attempt in range(MAX_ATTEMPTS):
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data=data,
                    files=files,
                )

            if resp.status_code < 400:
                payload = resp.json()
                text = (payload.get("text") or "").strip()
                logger.info("STT ok: %d chars", len(text))
                return text

            body = resp.text or ""
            logger.warning(
                "STT failed (%s) attempt %s/%s: %s",
                resp.status_code,
                attempt + 1,
                MAX_ATTEMPTS,
                body[:200],
            )

            # Rate limit / transient server error -> back off and retry.
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = self._describe(resp.status_code, body)
                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(self._retry_delay(resp, attempt))
                    continue
                raise STTRequestError(last_error)

            # Permanent failures: explain the real cause immediately.
            raise STTRequestError(self._describe(resp.status_code, body))

        raise STTRequestError(last_error or "Transcription failed.")

    @staticmethod
    def _retry_delay(resp: httpx.Response, attempt: int) -> float:
        """Honour Retry-After when present, else exponential-ish backoff."""
        retry_after = resp.headers.get("Retry-After", "").strip()
        if retry_after:
            try:
                return min(float(retry_after), 20.0)
            except ValueError:
                pass
        return BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]

    @staticmethod
    def _describe(status: int, body: str) -> str:
        """Turn an HTTP failure into a message the UI can show the user."""
        low = body.lower()
        if status == 404 and "model" in low:
            return (
                "Speech-to-text model is not available on this provider. "
                "Set STT_MODEL in backend/.env (Groq: whisper-large-v3-turbo, OpenAI: whisper-1)."
            )
        if status in (401, 403):
            return "Speech-to-text authentication failed — check STT_API_KEY in backend/.env."
        if status == 429:
            return "Speech-to-text rate limit reached — waiting and retrying."
        if status == 400 and ("media file" in low or "decode" in low or "format" in low):
            return (
                "The recorded audio could not be decoded. "
                "Share the meeting tab again and tick \"Also share tab audio\"."
            )
        if status >= 500:
            return f"Speech-to-text service error (HTTP {status}). Try again in a moment."
        return f"Transcription failed (HTTP {status}). {body[:120]}"

    @staticmethod
    def filename_for(mime_type: str) -> str:
        """The file name must match the bytes: providers use it to sniff type.

        Sending WAV bytes named `clip.webm` (or labelling them as WebM in the
        multipart content-type) makes strict providers answer HTTP 400
        "could not process file".
        """
        mime = (mime_type or "").lower()
        if "wav" in mime or "x-wav" in mime or "wave" in mime:
            return "clip.wav"
        if "webm" in mime:
            return "clip.webm"
        if "ogg" in mime:
            return "clip.ogg"
        if "mp4" in mime or "m4a" in mime:
            return "clip.mp4"
        if "mpeg" in mime or "mp3" in mime:
            return "clip.mp3"
        if "flac" in mime:
            return "clip.flac"
        return "clip.wav"


class MockSTTProvider(STTProvider):
    """No-op provider: returns empty text so callers degrade gracefully."""

    name = "mock"

    @property
    def available(self) -> bool:
        return False

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
    ) -> str:
        return ""


def get_stt_provider() -> STTProvider:
    """Pick a working provider, or the mock one when nothing is configured."""
    settings = get_settings()
    if settings.stt_api_key or settings.llm_api_key:
        provider = GroqWhisperSTTProvider()
        if provider.available:
            return provider
    return MockSTTProvider()