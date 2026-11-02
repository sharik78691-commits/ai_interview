"""Questions are transcribed and answered in the language they were ASKED in.

Found 2026-11-01 (user report: "STT converts every question to English").
Measured with real audio (Kokoro Hindi voices, Windows en-IN voices):

1. STT_LANGUAGE defaulted to "en", so Whisper TRANSLATED Hindi speech, often
   losing the meaning: "आपने Kafka और RabbitMQ में से Kafka क्यों चुना?" came
   back as "You have to ask Kafka and Rabbit MQ from Kafka." Auto-detect kept
   the spoken language and still heard Indian-accented English as English.
2. Auto-detect alone heard Roman-style Hinglish as URDU (Arabic script); such
   a clip is transcribed again in the closest allowed language (Hindi).
3. is_stt_hallucination() kept only a-z, so every pure-Devanagari transcript
   was dropped as a silence artefact; classify() also missed Devanagari
   request verbs ("बताइए", "समझाइए"), backchannels and audio checks.
4. The LLM prompt demanded "spoken English"; a strict model (DeepSeek)
   answered a Hindi question in English.
"""
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from app.providers.stt import provider as stt_mod
from app.providers.stt.provider import is_stt_hallucination, language_code, retranscribe_language
from app.services.question_service import ANSWER, NOISE, PARTIAL, classify, same_utterance
from tests.test_turn_taking import _asked, _clip, _run, _until

# ------------------------------------------------------------ STT requests


class _Ctx:
    def __init__(self, handler) -> None:
        self.transport = httpx.MockTransport(handler)

    async def __aenter__(self):
        self.client = httpx.AsyncClient(transport=self.transport)
        return self.client

    async def __aexit__(self, *exc):
        await self.client.aclose()


def _field(request: httpx.Request, name: str) -> str | None:
    """A multipart form field's value (None when absent)."""
    body = request.content
    marker = f'name="{name}"\r\n\r\n'.encode()
    at = body.find(marker)
    if at < 0:
        return None
    start = at + len(marker)
    return body[start:body.find(b"\r\n", start)].decode()


async def _transcribe(replies, *, language="auto", allowed=("en", "hi"), reports_language=True):
    """Run one transcription against scripted replies; return (text, requests)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=replies[min(len(seen), len(replies)) - 1])

    p = stt_mod.GroqWhisperSTTProvider(
        api_key="k", base_url="https://stt.test/v1", model="m", reports_language=reports_language
    )
    settings = SimpleNamespace(stt_language=language, stt_languages=allowed)
    with patch.object(stt_mod, "shared_http", lambda: _Ctx(handler)), \
            patch.object(stt_mod, "get_settings", return_value=settings):
        text = await p.transcribe(b"RIFF....WAVEdata", mime_type="audio/wav")
    return text, seen


@pytest.mark.asyncio
async def test_auto_mode_keeps_the_spoken_language_in_one_request() -> None:
    hindi = "आपने Kafka और RabbitMQ में से Kafka क्यों चुना?"
    text, seen = await _transcribe([{"text": hindi, "language": "hi"}])
    assert text == hindi
    assert len(seen) == 1
    assert _field(seen[0], "language") is None  # never forced to English
    assert _field(seen[0], "response_format") == "verbose_json"


@pytest.mark.parametrize("reported", ["ur", "Urdu"])
@pytest.mark.asyncio
async def test_hindi_heard_as_urdu_is_transcribed_again_as_hindi(reported) -> None:
    text, seen = await _transcribe([
        {"text": "جاوہ میں پولی موفیزم کیا ہوتا ہے؟", "language": reported},
        {"text": "जावा में polymorphism क्या होता है?"},
    ])
    assert text == "जावा में polymorphism क्या होता है?"
    assert len(seen) == 2
    assert _field(seen[1], "language") == "hi"
    assert _field(seen[1], "response_format") == "json"


@pytest.mark.asyncio
async def test_unrelated_misdetection_falls_back_to_the_first_allowed_language() -> None:
    text, seen = await _transcribe([
        {"text": "Iawn, gwych.", "language": "welsh"},
        {"text": "Okay, great."},
    ])
    assert text == "Okay, great." and _field(seen[1], "language") == "en"


@pytest.mark.asyncio
async def test_english_reported_by_name_is_accepted_as_is() -> None:
    text, seen = await _transcribe([{"text": "What is a deadlock?", "language": "English"}])
    assert text == "What is a deadlock?" and len(seen) == 1


@pytest.mark.asyncio
async def test_forced_language_still_works_exactly_as_before() -> None:
    """STT_LANGUAGE=en (an explicit setting) keeps the old request shape."""
    text, seen = await _transcribe([{"text": "What is a deadlock?"}], language="en")
    assert text == "What is a deadlock?"
    assert _field(seen[0], "language") == "en"
    assert _field(seen[0], "response_format") == "json"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_model_without_verbose_json_auto_detects_without_checking() -> None:
    """gpt-4o-mini-transcribe accepts json/text only."""
    text, seen = await _transcribe([{"text": "डेडलॉक क्या होता है?"}], reports_language=False)
    assert text == "डेडलॉक क्या होता है?"
    assert _field(seen[0], "response_format") == "json"
    assert _field(seen[0], "language") is None


def test_gpt4o_mini_transcribe_is_built_without_verbose_json() -> None:
    spec = next(sp for sp in stt_mod._stt_specs() if sp["key"] == "gpt-4o-mini-transcribe")
    assert spec["reports_language"] is False


def test_language_helpers() -> None:
    assert language_code("Hindi") == "hi" and language_code("hi") == "hi"
    assert language_code("") == "" and language_code("klingon") == ""
    assert retranscribe_language("hi", ("en", "hi")) is None
    assert retranscribe_language("", ("en", "hi")) is None  # unknown: keep the text
    assert retranscribe_language("ur", ("en", "hi")) == "hi"
    assert retranscribe_language("ur", ("en",)) == "en"
    assert retranscribe_language("ja", ("en", "hi")) == "en"


def test_default_settings_are_auto_with_english_and_hindi(monkeypatch) -> None:
    from app.core.config import Settings

    monkeypatch.delenv("STT_LANGUAGE", raising=False)
    monkeypatch.delenv("STT_LANGUAGES", raising=False)
    s = Settings()
    assert s.stt_language == "auto"
    assert s.stt_languages == ("en", "hi")
    monkeypatch.setenv("STT_LANGUAGES", " EN, hi ,ta ")
    assert Settings().stt_languages == ("en", "hi", "ta")


# ------------------------------------------------ Devanagari in the pipeline


@pytest.mark.parametrize(
    "text, verdict",
    [
        ("मुझे अपने पिछले प्रोजेक्ट के बारे में बताए।", ANSWER),
        ("अपने बारे में बताइए।", ANSWER),                      # tell me about yourself
        ("SOLID principles समझाइए", ANSWER),
        ("HashMap और HashTable में क्या अंतर है", ANSWER),
        ("Spring Boot के बारे में बताओ", ANSWER),
        ("आपने Kafka और Rabbit MQ में से Kafka क्यों चुना?", ANSWER),
        ("ठीक है।", NOISE),
        ("हाँ जी, बिल्कुल।", NOISE),
        ("क्या आपको मेरी आवाज़ आ रही है?", NOISE),              # can you hear me?
        ("क्या आप explain कर सकते हैं कि", PARTIAL),           # cut off on "that"
    ],
)
def test_devanagari_utterances_are_classified_like_english_ones(text, verdict) -> None:
    assert classify(text) == verdict


@pytest.mark.parametrize("text", [
    "मुझे अपने पिछले प्रोजेक्ट के बारे में बताए।",
    "डेडलॉक क्या होता है?",
])
def test_devanagari_questions_are_not_silence_artefacts(text) -> None:
    assert not is_stt_hallucination(text)


@pytest.mark.parametrize("text", ["धन्यवाद।", "बहुत बहुत धन्यवाद।", "Thank you.", "", "..."])
def test_silence_artefacts_in_any_script_are_dropped(text) -> None:
    assert is_stt_hallucination(text)


def test_duplicate_matching_compares_whole_hindi_words() -> None:
    assert not same_utterance("डेडलॉक क्या होता है?", "थ्रेड क्या होता है?")
    assert same_utterance("आपने Kafka क्यों चुना?", "Kafka क्यों चुना आपने")


@patch("app.api.websocket.get_stt_provider")
@patch("app.api.websocket.AIService")
def test_socket_answers_a_pure_devanagari_question(ai_cls, stt_factory) -> None:
    question = "मुझे अपने पिछले प्रोजेक्ट के बारे में बताए।"

    def script(ws):
        _clip(ws, b"hindi-question")
        return _until(ws, lambda m: m.get("type") == "ai_guidance")

    frames = _run(ai_cls, stt_factory, [question], script)
    assert any(f.get("type") == "stt_transcript" and f.get("text") == question for f in frames)
    assert _asked(ai_cls) == [question]


# ------------------------------------------------------------ LLM language


def test_answer_prompt_mirrors_the_question_language() -> None:
    from app.providers.llm.provider import build_system_prompt

    for depth in ("default", "short", "medium", "long"):
        prompt = build_system_prompt(depth)
        assert "LANGUAGE" in prompt
        assert "Never translate the answer into English" in prompt
        assert "spoken English" not in prompt


def test_follow_up_prompt_mirrors_the_question_language() -> None:
    from app.providers.llm.provider import build_followup_prompt

    prompt = build_followup_prompt("medium")
    assert "never translate to English" in prompt
    assert "spoken English" not in prompt
