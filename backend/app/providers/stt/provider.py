"""Concrete STT providers.

- GroqWhisperSTTProvider: real transcription (whisper-large-v3-turbo).
- GeminiTranscribeSTTProvider / GeminiLiveSTTProvider: Google Gemini models
  (native generateContent / Live API WebSocket).
- MockSTTProvider: used when no STT credentials exist, so the UI still works
  and can explain that transcription is unavailable.
"""
import asyncio
import logging
import re
import time

import httpx

from app.core.config import get_settings
from app.core.http import prewarm, shared_http
from app.providers.stt.base import STTProvider

logger = logging.getLogger(__name__)

# Transcription is called once per spoken question, so a burst of questions can
# hit the provider's rate limit. Retry a few times before giving up — but keep
# the waits SHORT: this is a LIVE interview, and a user staring at silence for
# 5s of backoff notices far more than a dropped clip (the UI can always fall
# back to pasting the question).
# (Per instance: see STTProvider.max_attempts.)
MAX_ATTEMPTS = STTProvider.max_attempts
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
        # Whisper's Hindi silence artefacts (auto-detect, 2026-11-01).
        "धन्यवाद",
        "शुक्रिया",
        "बहुत बहुत धन्यवाद",
        "सब्सक्राइब करें",
        "चैनल को सब्सक्राइब करें",
    }
)

#: A word in any script. Devanagari vowel signs and viramas are combining
#: marks, which \w does not match, so the block is listed explicitly — minus
#: the danda (। ॥, U+0964/5), which is punctuation.
_WORD_RE = re.compile(r"[\wऀ-ॣ०-ॿ']+")


def is_stt_hallucination(text: str) -> bool:
    """True when a transcript is just a known Whisper silence artefact.

    Script-agnostic: it used to keep only a-z, so EVERY Hindi transcript
    normalised to nothing and was dropped as silence.
    """
    norm = [w for w in _WORD_RE.findall((text or "").lower()) if w.strip("'")]
    if not norm:
        return True
    return " ".join(norm) in _HALLUCINATIONS


#: Whisper reports the detected language as a code ("hi": DeepInfra) or an
#: English name ("Hindi": Groq). Names it may report for interview audio.
_LANGUAGE_CODES = {
    "english": "en", "hindi": "hi", "urdu": "ur", "marathi": "mr", "nepali": "ne",
    "sanskrit": "sa", "punjabi": "pa", "sindhi": "sd", "bengali": "bn",
    "gujarati": "gu", "tamil": "ta", "telugu": "te", "kannada": "kn",
    "malayalam": "ml", "assamese": "as", "welsh": "cy", "japanese": "ja",
    "chinese": "zh", "korean": "ko", "russian": "ru", "arabic": "ar",
    "persian": "fa", "spanish": "es", "french": "fr", "german": "de",
    "portuguese": "pt", "italian": "it", "indonesian": "id", "malay": "ms",
    "turkish": "tr", "vietnamese": "vi", "thai": "th", "dutch": "nl",
    "polish": "pl",
}
#: A misdetected language -> the one it is really heard for. Hindi and Urdu
#: are one spoken language in two scripts (Hinglish came back in Arabic
#: script, 2026-11-01); Marathi, Nepali, Sanskrit, Punjabi and Sindhi share
#: Hindi's sounds.
_LANGUAGE_FALLBACK = {"ur": "hi", "mr": "hi", "ne": "hi", "sa": "hi", "pa": "hi", "sd": "hi"}


def language_code(raw: object) -> str:
    """Detected language as a lower-case code ("" when unknown)."""
    value = str(raw or "").strip().lower()
    if not value:
        return ""
    return _LANGUAGE_CODES.get(value, value if len(value) <= 3 else "")


def retranscribe_language(detected: str, allowed: tuple[str, ...]) -> str | None:
    """The language to transcribe again in, or None when ``detected`` is fine."""
    if not detected or not allowed or detected in allowed:
        return None
    closest = _LANGUAGE_FALLBACK.get(detected)
    return closest if closest in allowed else allowed[0]


#: Sound annotations Whisper writes for non-speech: "*thud*", "[Music]",
#: "[BLANK_AUDIO]", "(coughs)". Parentheses only count when lower-case, so an
#: acronym such as "dependency injection (DI)" survives.
_SOUND_TAG_RE = re.compile(r"\*[^*\n]{1,40}\*|\[[^\]\n]{1,40}\]|\([a-z][a-z ,'-]{0,38}\)")


def strip_sound_tags(text: str) -> str:
    """Remove non-speech annotations so a cough or a keyboard burst never
    becomes part of a question (or a question of its own)."""
    if not text:
        return ""
    return re.sub(r"\s{2,}", " ", _SOUND_TAG_RE.sub(" ", text)).strip()


#: Provider bodies that mean "the account cannot pay", not "slow down" or
#: "bad key": OpenAI answers 429 insufficient_quota, Gemini 403 "outstanding
#: balance" (both seen 2026-10-31). Retrying cannot fix them. Specific
#: phrases only: a plain Groq rate limit ("Rate limit reached … try again in
#: 2s … Upgrade at console.groq.com/settings/billing") mentions billing too,
#: and must stay retryable.
_BILLING_MARKERS = (
    "insufficient_quota", "credit_balance", "no credits", "outstanding balance",
    "make a payment", "billing account", "exceeded your current quota",
)
_RATE_LIMIT_MARKERS = ("rate limit reached", "try again in", "rate_limit_exceeded")
STT_BILLING_MESSAGE = (
    "Speech-to-text provider account is out of credits / has a billing problem. "
    "Ask the admin to top it up or switch the STT model."
)


def is_billing_error(body: str) -> bool:
    low = (body or "").lower()
    if any(marker in low for marker in _RATE_LIMIT_MARKERS):
        return False
    return any(marker in low for marker in _BILLING_MARKERS)


class STTUnavailableError(RuntimeError):
    """Raised when no speech-to-text backend is configured at all."""


class STTRequestError(RuntimeError):
    """Raised when a provider call fails (wrong model, network, quota...)."""


class GroqWhisperSTTProvider(STTProvider):
    """Whisper transcription via any OpenAI-compatible /audio/transcriptions."""

    name = "whisper"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        send_prompt: bool = True,
        reports_language: bool = True,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.stt_api_key or settings.llm_api_key
        self.base_url = (base_url or settings.stt_base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.stt_model
        # Some hosts mis-handle the vocabulary prompt (see "whisper-deepinfra").
        self.send_prompt = send_prompt
        # Whisper hosts return the detected language with verbose_json;
        # gpt-4o-*-transcribe only accept json/text (no language to check).
        self.reports_language = reports_language

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

        settings = get_settings()
        data = {
            "model": self.model,
            "response_format": "json",
            "temperature": "0",
        }
        if self.send_prompt:
            data["prompt"] = STT_PROMPT
        forced = "" if settings.stt_language in ("", "auto") else settings.stt_language
        if forced:
            data["language"] = forced
        check_language = not forced and self.reports_language
        if check_language:
            data["response_format"] = "verbose_json"  # carries the detected language
        # Name the part after what the bytes actually are. Strict providers use
        # the filename/content-type to pick a decoder, so a mismatched label
        # (e.g. WAV bytes sent as clip.webm) is rejected as an invalid file.
        # mime_type is authoritative; `filename` stays for interface parity.
        send_name = self.filename_for(mime_type)
        files = {"file": (send_name, audio_bytes, mime_type)}
        logger.info(
            "STT request: %d bytes, mime=%s, part=%s, model=%s, language=%s",
            len(audio_bytes),
            mime_type,
            send_name,
            self.model,
            forced or "auto",
        )

        t0 = time.perf_counter()
        payload = await self._post(data, files)
        text = (payload.get("text") or "").strip()
        if check_language:
            detected = language_code(payload.get("language"))
            again = retranscribe_language(detected, settings.stt_languages)
            if again:
                # Wrong script for the speech (Hindi heard as Urdu): one more
                # request in the language it really is, never a translation.
                logger.info("STT heard %r (allowed %s): transcribing again as %r",
                            detected, ",".join(settings.stt_languages), again)
                retry = dict(data, language=again, response_format="json")
                text = ((await self._post(retry, files)).get("text") or "").strip()
        logger.info("STT ok: %d chars in %.2fs", len(text), time.perf_counter() - t0)
        return text

    async def _post(self, data: dict, files: dict) -> dict:
        """One /audio/transcriptions call with the retry budget; the JSON body."""
        last_error: str = ""
        attempts = self.max_attempts
        for attempt in range(attempts):
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
                return resp.json()

            body = resp.text or ""
            logger.warning(
                "STT failed (%s) attempt %s/%s: %s",
                resp.status_code,
                attempt + 1,
                attempts,
                body[:200],
            )

            # Rate limit / transient server error -> back off and retry.
            # (Out of credits also answers 429, but no retry can fix it.)
            if (resp.status_code == 429 or resp.status_code >= 500) and not is_billing_error(body):
                last_error = self._describe(resp.status_code, body)
                if attempt < attempts - 1:
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
        if status in (402, 403, 429) and is_billing_error(body):
            return STT_BILLING_MESSAGE
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
        attempts = self.max_attempts
        for attempt in range(attempts):
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
                attempts,
                body_text[:200],
            )

            if is_billing_error(body_text):
                raise STTRequestError(STT_BILLING_MESSAGE)
            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = f"Speech-to-text service error (HTTP {resp.status_code})."
                if attempt < attempts - 1:
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


GEMINI_LIVE_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
# 100 ms of 16-bit mono audio per realtimeInput message (at the clip's rate).
_LIVE_CHUNK_SECONDS = 0.1
# WebSocket close codes that mean "transient — try again".
_LIVE_RETRY_CLOSE_CODES = {1011, 1012, 1013, 1014}


def wav_to_pcm16_mono(audio_bytes: bytes) -> tuple[bytes, int] | None:
    """(raw PCM, sample rate) for a 16-bit mono WAV clip, else None."""
    import io
    import wave

    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getcomptype() != "NONE":
                return None
            return w.readframes(w.getnframes()), w.getframerate()
    except (wave.Error, EOFError):
        return None


def _live_connect(api_key: str):
    """Open the Live API socket (separate so tests can swap it out).

    The key goes in a header, never the URL, so it cannot leak via logs.
    """
    from websockets.asyncio.client import connect

    return connect(
        GEMINI_LIVE_URL,
        additional_headers={"x-goog-api-key": api_key},
        max_size=None,
        open_timeout=10,
    )


class GeminiLiveSTTProvider(STTProvider):
    """Transcription via Google's Gemini Live API (bidiGenerateContent).

    `gemini-3.5-transcribe-live` ONLY exposes bidiGenerateContent (a WebSocket
    stream), not generateContent. Each clip opens one Live session: the WAV's
    16-bit mono PCM is streamed as realtimeInput, then audioStreamEnd. The
    server answers with provisional `interimInputTranscription` updates and a
    final `inputTranscription`; only FINAL text is returned, so a provisional
    transcript can never be treated as the question.

    Clips that are not 16-bit mono WAV (e.g. the WebM fallback) are sent to
    the non-live Gemini Transcribe model instead, so no clip is dropped.
    """

    name = "gemini-live"

    def __init__(self, api_key: str = "", model: str = "") -> None:
        settings = get_settings()
        self.api_key = api_key or settings.gemini_api_key
        self.model = model or settings.stt_model_4

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
        decoded = wav_to_pcm16_mono(audio_bytes)
        if decoded is None:
            logger.info("STT (gemini-live): %s clip is not PCM WAV, using %s", mime_type, get_settings().stt_model_3)
            return await GeminiTranscribeSTTProvider(api_key=self.api_key).transcribe(
                audio_bytes, filename=filename, mime_type=mime_type
            )
        pcm, rate = decoded
        if not pcm:
            return ""

        logger.info(
            "STT request (gemini-live): %d bytes PCM @ %d Hz, model=%s", len(pcm), rate, self.model
        )
        t0 = time.perf_counter()
        last_error = ""
        attempts = self.max_attempts
        for attempt in range(attempts):
            try:
                text = await asyncio.wait_for(
                    self._session(pcm, rate), timeout=REQUEST_TIMEOUT_SECONDS
                )
                logger.info(
                    "STT ok (gemini-live): %d chars in %.2fs", len(text), time.perf_counter() - t0
                )
                return text
            except STTRequestError:
                raise
            except Exception as exc:  # network, timeout, transient close
                code = getattr(getattr(exc, "rcvd", None), "code", None)
                reason = str(getattr(getattr(exc, "rcvd", None), "reason", "") or "")
                logger.warning(
                    "STT failed (gemini-live) attempt %s/%s: %s %s",
                    attempt + 1,
                    attempts,
                    type(exc).__name__,
                    (reason or str(exc))[:200].replace(self.api_key, "[redacted]"),
                )
                if is_billing_error(reason):
                    raise STTRequestError(STT_BILLING_MESSAGE) from None
                if code == 1008 or "api key" in reason.lower():
                    raise STTRequestError(
                        "Speech-to-text authentication failed — check GEMINI_API_KEY in backend/.env."
                    ) from None
                if code is not None and code not in _LIVE_RETRY_CLOSE_CODES:
                    raise STTRequestError(
                        f"Transcription failed (Live API closed: {reason[:120] or code})."
                    ) from None
                last_error = "Speech-to-text service error. Try again in a moment."
                if attempt < attempts - 1:
                    await asyncio.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
        raise STTRequestError(last_error or "Transcription failed.")

    async def _session(self, pcm: bytes, rate: int) -> str:
        """One Live session: setup -> stream PCM -> collect FINAL transcription."""
        import base64
        import json

        async with _live_connect(self.api_key) as ws:
            await ws.send(
                json.dumps({"setup": {"model": f"models/{self.model}", "inputAudioTranscription": {}}})
            )
            first = json.loads(await ws.recv())
            if "setupComplete" not in first:
                raise STTRequestError("Transcription failed (Live API setup was not accepted).")

            chunk = max(2, int(rate * _LIVE_CHUNK_SECONDS) * 2)
            mime = f"audio/pcm;rate={rate}"
            for i in range(0, len(pcm), chunk):
                await ws.send(
                    json.dumps(
                        {
                            "realtimeInput": {
                                "audio": {
                                    "data": base64.b64encode(pcm[i : i + chunk]).decode(),
                                    "mimeType": mime,
                                }
                            }
                        }
                    )
                )
            await ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))

            finals: list[str] = []
            async for raw in ws:
                msg = json.loads(raw)
                content = msg.get("serverContent") or {}
                # interimInputTranscription is provisional and deliberately ignored.
                final = (content.get("inputTranscription") or {}).get("text")
                if final:
                    finals.append(str(final).strip())
                if content.get("generationComplete") or content.get("turnComplete"):
                    break
            return " ".join(t for t in finals if t).strip()


def get_stt_provider() -> STTProvider:
    """Pick the enabled+default STT provider, or mock when none is usable.

    Five models are offered (Groq Whisper + OpenAI gpt-4o-mini-transcribe +
    Google Gemini 3.5 Transcribe + Google Gemini 3.5 Transcribe Live +
    DeepInfra Whisper).
    Admin can enable/disable either and set a default via the ai_model_config
    table (see /api/admin/stt-models). A model is used only when it is enabled
    AND its provider key is present.
    """
    sp = _selected_stt_spec()
    if sp is not None:
        # Gemini transcription uses the native generateContent API (or the
        # Live WebSocket API); everything else rides the OpenAI-compatible
        # /audio/transcriptions path.
        if sp.get("api_style") == "gemini_live":
            return GeminiLiveSTTProvider(api_key=sp["api_key"], model=sp["model_id"])
        if sp.get("api_style") == "gemini_native":
            return GeminiTranscribeSTTProvider(
                api_key=sp["api_key"], base_url=sp["base_url"], model=sp["model_id"]
            )
        return GroqWhisperSTTProvider(
            api_key=sp["api_key"],
            base_url=sp["base_url"],
            model=sp["model_id"],
            send_prompt=sp.get("send_prompt", True),
            reports_language=sp.get("reports_language", True),
        )
    return MockSTTProvider()


def _selected_stt_spec() -> dict | None:
    """The STT model actually in use: the admin default when it is enabled and
    has a key, else the next usable one; None when none is usable (mock)."""
    specs = {sp["key"]: sp for sp in _stt_specs()}
    overrides = _stt_overrides()
    default_key = default_stt_key()
    ordered = [default_key] + [k for k in specs if k != default_key]
    for key in ordered:
        sp = specs[key]
        ov = overrides.get(key)
        enabled = ov.enabled if ov else True
        if enabled and sp["api_key"]:
            return sp
    return None


def live_stream_selected() -> bool:
    """True only when the STT in use is Gemini Transcribe Live — the one
    streaming model. The word-by-word transcript then uses it; with any other
    STT the live transcript comes from snapshot previews through that STT."""
    sp = _selected_stt_spec()
    return sp is not None and sp.get("api_style") == "gemini_live"


# --- STT model catalog + admin selection (enable/disable + default) ---

_STT_DEFAULT_KEY = "whisper"
DEEPINFRA_OPENAI_URL = "https://api.deepinfra.com/v1/openai"


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
            # json/text only (no verbose_json), so no detected language to check.
            "reports_language": False,
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
        {
            "key": "gemini-3.5-transcribe-live",
            "provider": "Google",
            "display_name": "Gemini 3.5 Transcribe Live — Google",
            "model_id": s.stt_model_4,
            "base_url": GEMINI_LIVE_URL,
            "api_key": s.gemini_api_key,
            # Only exposes bidiGenerateContent (Live API over a WebSocket).
            "api_style": "gemini_live",
        },
        {
            # Same Whisper large-v3-turbo as Groq, served by DeepInfra over the
            # OpenAI-compatible /audio/transcriptions path.
            "key": "whisper-deepinfra",
            "provider": "DeepInfra",
            "display_name": "Whisper — DeepInfra",
            "model_id": s.stt_model_5,
            "base_url": DEEPINFRA_OPENAI_URL,
            "api_key": s.deepinfra_api_key,
            # DeepInfra treats the vocabulary prompt as preceding speech and
            # DROPS the start of the question ("Can you explain X, and how…"
            # came back as "And how…"). Measured 2026-10-20: without the
            # prompt the full question is returned.
            "send_prompt": False,
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
    # Every real provider (OpenAI-compat, Gemini native, Gemini Live) exposes `.model`.
    if provider.available and hasattr(provider, "model"):
        return provider.model
    return None


async def prewarm_stt() -> bool:
    """Warm the pooled connection to the active STT host (interviewer audio start).

    Only the OpenAI-compatible / Gemini-native HTTP providers are warmed; the
    Gemini Live provider opens its own WebSocket per clip, and the mock has no host.
    """
    provider = get_stt_provider()
    base_url = getattr(provider, "base_url", "") if getattr(provider, "available", False) else ""
    if not base_url or not base_url.startswith("http"):
        return False
    return await prewarm(base_url)
