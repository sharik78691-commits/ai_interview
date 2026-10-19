"""gemini-3.5-transcribe-live: Gemini Live API (bidiGenerateContent) STT."""
import base64
import io
import json
import wave
from unittest.mock import patch

import pytest

from app.providers.stt import provider as stt
from app.providers.stt.provider import (
    GeminiLiveSTTProvider,
    STTRequestError,
    get_stt_provider,
    list_stt_models,
    wav_to_pcm16_mono,
)


def _wav(seconds: float = 0.35, rate: int = 16000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(rate * seconds) * channels)
    return buf.getvalue()


class _Closed(Exception):
    """Stands in for websockets.ConnectionClosed (carries .rcvd)."""

    def __init__(self, code: int, reason: str = ""):
        super().__init__(reason)
        self.rcvd = type("Close", (), {"code": code, "reason": reason})()


class FakeLive:
    """Fake Live socket: records what the client sends, replays server frames."""

    def __init__(self, frames: list[dict], fail: Exception | None = None):
        self.frames = frames
        self.fail = fail
        self.sent: list[dict] = []

    async def __aenter__(self):
        if self.fail:
            raise self.fail
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, data: str):
        self.sent.append(json.loads(data))

    async def recv(self):
        return json.dumps({"setupComplete": {}})

    def __aiter__(self):
        async def gen():
            for f in self.frames:
                yield json.dumps(f)

        return gen()


FINAL_FRAMES = [
    {"serverContent": {}, "voiceActivity": {"type": "ACTIVITY_START"}},
    {"serverContent": {"interimInputTranscription": {"text": "Can you"}}},
    {"serverContent": {"interimInputTranscription": {"text": "Can you explain the"}}},
    {"serverContent": {"inputTranscription": {"text": "Can you explain the Saga pattern?"}}},
    {"serverContent": {"generationComplete": True}},
]


async def test_live_streams_pcm_and_returns_only_the_final_transcript():
    fake = FakeLive(FINAL_FRAMES)
    p = GeminiLiveSTTProvider(api_key="g-test", model="gemini-3.5-transcribe-live")
    with patch.object(stt, "_live_connect", return_value=fake) as connect:
        text = await p.transcribe(_wav(), mime_type="audio/wav")

    assert text == "Can you explain the Saga pattern?"
    connect.assert_called_once_with("g-test")  # key travels as a header arg, not in a URL
    setup = fake.sent[0]["setup"]
    assert setup["model"] == "models/gemini-3.5-transcribe-live"
    assert setup["inputAudioTranscription"] == {}
    audio = [m["realtimeInput"]["audio"] for m in fake.sent[1:-1]]
    assert {a["mimeType"] for a in audio} == {"audio/pcm;rate=16000"}
    # The WAV header is stripped: exactly the PCM samples are streamed.
    pcm = b"".join(base64.b64decode(a["data"]) for a in audio)
    assert pcm == wav_to_pcm16_mono(_wav())[0]
    assert fake.sent[-1] == {"realtimeInput": {"audioStreamEnd": True}}


async def test_provisional_interim_text_is_never_returned_as_final():
    frames = [
        {"serverContent": {"interimInputTranscription": {"text": "Can you explain"}}},
        {"serverContent": {"generationComplete": True}},
    ]
    p = GeminiLiveSTTProvider(api_key="g-test")
    with patch.object(stt, "_live_connect", return_value=FakeLive(frames)):
        assert await p.transcribe(_wav(), mime_type="audio/wav") == ""


async def test_non_wav_clip_falls_back_to_rest_gemini_transcribe():
    p = GeminiLiveSTTProvider(api_key="g-test")
    seen = {}

    async def fake_rest(self, audio_bytes, filename="audio.wav", mime_type="audio/wav"):
        seen["mime"] = mime_type
        return "Tell me about yourself."

    with patch.object(stt, "_live_connect") as connect, patch.object(
        stt.GeminiTranscribeSTTProvider, "transcribe", fake_rest
    ):
        text = await p.transcribe(b"\x1a\x45\xdf\xa3webm-bytes", mime_type="audio/webm")
    assert text == "Tell me about yourself."
    assert seen["mime"] == "audio/webm"
    connect.assert_not_called()


def test_stereo_or_8bit_wav_is_not_sent_as_pcm16_mono():
    assert wav_to_pcm16_mono(_wav(channels=2)) is None
    assert wav_to_pcm16_mono(b"not a wav") is None
    pcm, rate = wav_to_pcm16_mono(_wav(rate=24000))
    assert rate == 24000 and len(pcm) == int(24000 * 0.35) * 2


async def test_auth_failure_is_reported_without_retry():
    attempts = []

    def connect(_key):
        attempts.append(1)
        return FakeLive([], fail=_Closed(1008, "API key not valid. Please pass a valid API key."))

    p = GeminiLiveSTTProvider(api_key="g-test")
    with patch.object(stt, "_live_connect", side_effect=connect):
        with pytest.raises(STTRequestError, match="authentication failed"):
            await p.transcribe(_wav(), mime_type="audio/wav")
    assert len(attempts) == 1


async def test_transient_failure_retries_then_succeeds():
    calls = []

    def connect(_key):
        calls.append(1)
        if len(calls) == 1:
            return FakeLive([], fail=_Closed(1011, "Internal error"))
        return FakeLive(FINAL_FRAMES)

    p = GeminiLiveSTTProvider(api_key="g-test")
    with patch.object(stt, "_live_connect", side_effect=connect), patch.object(
        stt, "BACKOFF_SECONDS", (0.0, 0.0)
    ):
        assert await p.transcribe(_wav(), mime_type="audio/wav") == "Can you explain the Saga pattern?"
    assert len(calls) == 2


async def test_missing_key_is_unavailable():
    p = GeminiLiveSTTProvider(api_key="")
    with patch.object(p, "api_key", ""):
        assert p.available is False


def test_catalog_lists_live_model_without_secrets():
    models = {m["key"]: m for m in list_stt_models()}
    live = models["gemini-3.5-transcribe-live"]
    assert live["provider"] == "Google"
    assert live["modelId"] == "gemini-3.5-transcribe-live"
    assert "api_key" not in live
    # Existing models are untouched.
    assert {"whisper", "gpt-4o-mini-transcribe", "gemini-3.5-transcribe"} <= set(models)


def test_factory_returns_live_provider_when_it_is_the_default():
    default = type("Ov", (), {"enabled": True, "is_default": True})()
    with patch.object(stt, "_stt_overrides", return_value={"gemini-3.5-transcribe-live": default}), patch(
        "app.providers.stt.provider.get_settings"
    ) as gs:
        s = gs.return_value
        s.gemini_api_key = "g-test"
        s.stt_model_4 = "gemini-3.5-transcribe-live"
        provider = get_stt_provider()
    assert isinstance(provider, GeminiLiveSTTProvider)
    assert provider.model == "gemini-3.5-transcribe-live"
