"""Concrete STT providers.

- GroqWhisperSTTProvider: real transcription (whisper-large-v3-turbo).
- MockSTTProvider: used when no STT credentials exist, so the UI still works
  and can explain that transcription is unavailable.
"""
import asyncio
import logging
import time

import httpx

from app.core.config import get_settings
from app.core.http import shared_http
from app.providers.stt.base import STTProvider

logger = logging.getLogger(__name__)

# Transcription is called once per spoken question, so a burst of questions can
# hit the provider's rate limit. Retry a few times before giving up — but keep
# the waits SHORT: this is a LIVE interview, and a user staring at silence for
# 5s of backoff notices far more than a dropped clip (the UI can always fall
# back to pasting the question).
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (0.75, 1.5)
# Per-attempt timeout. Clips are at most ~30s of audio and whisper-turbo
# transcribes them in 1–3s, so 30s already covers a slow provider; the old 60s
# made a hung socket look like a frozen app.
REQUEST_TIMEOUT_SECONDS = 30.0
# Retry-After is honoured, but never for longer than this (a live question
# cannot wait 20s+ for a rate-limit window to roll over).
RETRY_AFTER_CAP_SECONDS = 8.0


# Whisper is biased towards words that appear in its prompt, so list the
# technical vocabulary interviewers actually use. Keeps "Kubernetes", "Kafka",
# "Saga" etc. from being heard as look-alike everyday words.
STT_PROMPT = (
    "Technical job interview question. Terms: microservices, Saga pattern, REST API, "
    "GraphQL, Kubernetes, Docker, Kafka, Redis, PostgreSQL, MongoDB, Spring Boot, "
    "Angular, React, TypeScript, JavaScript, Python, FastAPI, Java, hash map, "
    "binary tree, time complexity, Big O, system design, CI/CD, AWS."
)

# Whisper "hallucinates" these stock phrases on silence / room noise / the tail
# of a clip. They are never an interview question, so drop them before they
# reach question detection or the UI.
_HALLUCINATIONS = frozenset(
    {
        "thank you",
        "thanks",
        "thanks for watching",
        "thank you for watching",
        "thank you so much for watching",
        "please subscribe",
        "subscribe to my channel",
        "bye",
        "bye bye",
        "you",
        "uh",
        "um",
        "hmm",
        "mm hmm",
    }
)


def is_stt_hallucination(text: str) -> bool:
    """True when a transcript is just a known Whisper silence artefact."""
    import re

    norm = re.sub(r"[^a-z' ]+", " ", (text or "").lower()).split()
    if not norm:
        return True
    return " ".join(norm) in _HALLUCINATIONS


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
            "prompt": STT_PROMPT,
        }
        language = get_settings().stt_language
        if language:
            data["language"] = language
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

        t0 = time.perf_counter()
        last_error: str = ""
        for attempt in range(MAX_ATTEMPTS):
            # Shared pool: no per-call TLS handshake across retries.
            async with shared_http() as client:
                resp = await client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data=data,
                    files=files,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )

            if resp.status_code < 400:
                payload = resp.json()
                text = (payload.get("text") or "").strip()
                logger.info("STT ok: %d chars in %.2fs", len(text), time.perf_counter() - t0)
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
                return min(float(retry_after), RETRY_AFTER_CAP_SECONDS)
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


class GeminiTranscribeSTTProvider(STTProvider):
    """Transcription via Google's native Gemini generateContent endpoint.

    `gemini-3.5-transcribe` only exposes `generateContent` (it does not accept
    the OpenAI-compatible /audio/transcriptions path), so the clip is sent as
    inline audio data and the reply text is the transcript.
    """

    name = "gemini"

    def __init__(self, api_key: str = "", base_url: str = "", model: str = "") -> None:
        settings = get_settings()
        self.api_key = api_key or settings.gemini_api_key
        self.base_url = (
            (base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        )
        self.model = model or settings.stt_model_3

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
                "No Google Gemini credentials configured (set GEMINI_API_KEY in backend/.env)."
            )
        if not audio_bytes:
            return ""

        import base64

        inline = base64.b64encode(audio_bytes).decode()
        url = f"{self.base_url}/models/{self.model}:generateContent"
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": mime_type or "audio/wav",
                                "data": inline,
                            }
                        },
                        {
                            "text": (
                                "Transcribe this audio exactly as spoken. "
                                "Reply with only the transcript."
                            )
                        },
                    ]
                }
            ]
        }
        logger.info(
            "STT request (gemini): %d bytes, mime=%s, model=%s",
            len(audio_bytes),
            mime_type,
            self.model,
        )

        t0 = time.perf_counter()
        last_error: str = ""
        for attempt in range(MAX_ATTEMPTS):
            async with shared_http() as client:
                resp = await client.post(
                    url,
                    headers={"x-goog-api-key": self.api_key},
                    json=payload,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )

            if resp.status_code < 400:
                body = resp.json()
                candidates = body.get("candidates") or [{}]
                parts = (candidates[0].get("content") or {}).get("parts") or []
                chunks: list[str] = []
                for p in parts:
                    if not isinstance(p, dict):
                        continue
                    if p.get("text"):
                        chunks.append(str(p["text"]))
                        continue
                    # gemini-3.5-transcribe reports the transcript under
                    # audioTranscription.text rather than a plain text part.
                    at = p.get("audioTranscription")
                    if isinstance(at, dict) and at.get("text"):
                        chunks.append(str(at["text"]))
                text = " ".join(chunks).strip()
                logger.info(
                    "STT ok (gemini): %d chars in %.2fs",
                    len(text),
                    time.perf_counter() - t0,
                )
                return text

            body_text = resp.text or ""
            logger.warning(
                "STT failed (gemini) %s attempt %s/%s: %s",
                resp.status_code,
                attempt + 1,
                MAX_ATTEMPTS,
                body_text[:200],
            )

            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = f"Speech-to-text service error (HTTP {resp.status_code})."
                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(GroqWhisperSTTProvider._retry_delay(resp, attempt))
                    continue
                raise STTRequestError(last_error)
            if resp.status_code in (401, 403):
                raise STTRequestError(
                    "Speech-to-text authentication failed — check GEMINI_API_KEY in backend/.env."
                )
            raise STTRequestError(
                f"Transcription failed (HTTP {resp.status_code}). {body_text[:120]}"
            )

        raise STTRequestError(last_error or "Transcription failed.")


def get_stt_provider() -> STTProvider:
    """Pick the enabled+default STT provider, or mock when none is usable.

    Three models are offered (Groq Whisper + OpenAI gpt-4o-mini-transcribe +
    Google Gemini 3.5 Transcribe).
    Admin can enable/disable either and set a default via the ai_model_config
    table (see /api/admin/stt-models). A model is used only when it is enabled
    AND its provider key is present.
    """
    specs = {sp["key"]: sp for sp in _stt_specs()}
    overrides = _stt_overrides()
    default_key = default_stt_key()
    ordered = [default_key] + [k for k in specs if k != default_key]
    for key in ordered:
        sp = specs[key]
        ov = overrides.get(key)
        enabled = ov.enabled if ov else True
        if not enabled or not sp["api_key"]:
            continue
        # Gemini transcription uses the native generateContent API; everything
        # else rides the OpenAI-compatible /audio/transcriptions path.
        if sp.get("api_style") == "gemini_native":
            return GeminiTranscribeSTTProvider(
                api_key=sp["api_key"], base_url=sp["base_url"], model=sp["model_id"]
            )
        return GroqWhisperSTTProvider(
            api_key=sp["api_key"], base_url=sp["base_url"], model=sp["model_id"]
        )
    return MockSTTProvider()


# --- STT model catalog + admin selection (enable/disable + default) ---

_STT_DEFAULT_KEY = "whisper"


def _stt_specs() -> list[dict]:
    """The selectable STT models. Secrets stay backend-side only."""
    s = get_settings()
    return [
        {
            "key": "whisper",
            "provider": "Groq",
            "display_name": "Whisper — Groq",
            "model_id": s.stt_model,
            "base_url": (s.stt_base_url or s.llm_base_url).rstrip("/"),
            "api_key": s.stt_api_key or s.llm_api_key,
        },
        {
            "key": "gpt-4o-mini-transcribe",
            "provider": "OpenAI",
            "display_name": "GPT-4o-mini Transcribe — OpenAI",
            "model_id": s.stt_model_2,
            "base_url": "https://api.openai.com/v1",
            "api_key": s.openai_api_key,
        },
        {
            "key": "gemini-3.5-transcribe",
            "provider": "Google",
            "display_name": "Gemini 3.5 Transcribe — Google",
            "model_id": s.stt_model_3,
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "api_key": s.gemini_api_key,
            # gemini-3.5-transcribe only exposes the native generateContent
            # method (not the OpenAI /audio/transcriptions path).
            "api_style": "gemini_native",
        },
    ]


def _stt_overrides() -> dict:
    """Admin flags (enabled/default) for STT models, from ai_model_config."""
    try:
        from app.db.session import SessionLocal
        from app.models.ai_model_config import AIModelConfig

        db = SessionLocal()
        try:
            keys = {sp["key"] for sp in _stt_specs()}
            return {r.key: r for r in db.query(AIModelConfig).all() if r.key in keys}
        finally:
            db.close()
    except Exception:
        return {}


def default_stt_key() -> str:
    """Resolve the default STT model (admin override -> built-in Whisper)."""
    for key, ov in _stt_overrides().items():
        if ov.is_default:
            return key
    return _STT_DEFAULT_KEY


def list_stt_models() -> list[dict]:
    """Public STT model list for the admin panel (no secrets)."""
    overrides = _stt_overrides()
    default_key = default_stt_key()
    result = []
    for sp in _stt_specs():
        ov = overrides.get(sp["key"])
        enabled = ov.enabled if ov else True
        result.append(
            {
                "key": sp["key"],
                "provider": sp["provider"],
                "displayName": sp["display_name"],
                "modelId": sp["model_id"],
                "available": bool(sp["api_key"]) and enabled,
                "enabled": enabled,
                "isDefault": sp["key"] == default_key,
            }
        )
    return result


def active_stt_model() -> str | None:
    """The model id currently serving transcription (None when disabled)."""
    provider = get_stt_provider()
    if isinstance(provider, MockSTTProvider):
        return None
    # Both real providers (OpenAI-compat + Gemini native) expose `.model`.
    if provider.available and hasattr(provider, "model"):
        return provider.model
    return None