"""DeepInfra Whisper (5th STT model): catalog entry + factory wiring."""
from unittest.mock import patch

from app.providers.stt import provider as stt
from app.providers.stt.provider import (
    DEEPINFRA_OPENAI_URL,
    GroqWhisperSTTProvider,
    MockSTTProvider,
    get_stt_provider,
    list_stt_models,
)


def test_catalog_lists_deepinfra_whisper_without_secrets():
    models = {m["key"]: m for m in list_stt_models()}
    di = models["whisper-deepinfra"]
    assert di["provider"] == "DeepInfra"
    assert di["modelId"] == "openai/whisper-large-v3-turbo"
    assert "api_key" not in di
    # Existing models are untouched and Whisper (Groq) stays the built-in default.
    assert {"whisper", "gpt-4o-mini-transcribe", "gemini-3.5-transcribe", "gemini-3.5-transcribe-live"} <= set(models)
    assert models["whisper"]["isDefault"] is True


def _settings(gs, deepinfra_key: str):
    s = gs.return_value
    s.stt_api_key = s.llm_api_key = s.openai_api_key = s.gemini_api_key = ""
    s.stt_base_url, s.llm_base_url = "", "https://api.groq.com/openai/v1"
    s.stt_model, s.stt_model_2, s.stt_model_3, s.stt_model_4 = "w", "g4", "g35", "g35l"
    s.deepinfra_api_key = deepinfra_key
    s.stt_model_5 = "openai/whisper-large-v3-turbo"


def test_factory_uses_deepinfra_endpoint_when_it_is_the_default():
    default = type("Ov", (), {"enabled": True, "is_default": True})()
    with patch.object(stt, "_stt_overrides", return_value={"whisper-deepinfra": default}), patch(
        "app.providers.stt.provider.get_settings"
    ) as gs:
        _settings(gs, "di-test")
        provider = get_stt_provider()
    assert isinstance(provider, GroqWhisperSTTProvider)
    assert provider.base_url == DEEPINFRA_OPENAI_URL
    assert provider.model == "openai/whisper-large-v3-turbo"
    assert provider.api_key == "di-test"


def test_deepinfra_without_key_is_skipped():
    default = type("Ov", (), {"enabled": True, "is_default": True})()
    with patch.object(stt, "_stt_overrides", return_value={"whisper-deepinfra": default}), patch(
        "app.providers.stt.provider.get_settings"
    ) as gs:
        _settings(gs, "")
        assert isinstance(get_stt_provider(), MockSTTProvider)


def _capture_post(provider):
    """Run transcribe() with a fake HTTP client; return the form data sent."""
    import asyncio

    sent = {}

    class _Resp:
        status_code = 200
        text = ""
        headers: dict = {}

        def json(self):
            return {"text": "Can you explain the Saga pattern?"}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kw):
            sent.update(kw.get("data") or {})
            return _Resp()

    with patch("app.providers.stt.provider.shared_http", return_value=_Client()):
        asyncio.run(provider.transcribe(b"RIFF....WAVE", mime_type="audio/wav"))
    return sent


def test_deepinfra_never_sends_the_vocabulary_prompt():
    """Regression: the prompt made DeepInfra drop the start of the question."""
    default = type("Ov", (), {"enabled": True, "is_default": True})()
    with patch.object(stt, "_stt_overrides", return_value={"whisper-deepinfra": default}), patch(
        "app.providers.stt.provider.get_settings"
    ) as gs:
        _settings(gs, "di-test")
        gs.return_value.stt_language = "en"
        provider = get_stt_provider()
        assert provider.send_prompt is False
        data = _capture_post(provider)
    assert "prompt" not in data
    assert data["model"] == "openai/whisper-large-v3-turbo"


def test_groq_whisper_still_sends_the_vocabulary_prompt():
    with patch("app.providers.stt.provider.get_settings") as gs:
        _settings(gs, "")
        gs.return_value.stt_language = "en"
        provider = GroqWhisperSTTProvider(api_key="g", base_url="https://api.groq.com/openai/v1", model="w")
        data = _capture_post(provider)
    assert data["prompt"] == stt.STT_PROMPT
