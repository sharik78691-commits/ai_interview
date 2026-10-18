"""Regression tests for STT accuracy helpers (no network)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.providers.stt.provider import (
    STT_PROMPT,
    GroqWhisperSTTProvider,
    is_stt_hallucination,
)


@pytest.mark.parametrize(
    "text",
    ["Thank you.", "thanks for watching!", " You ", "Bye-bye", "...", "", "Um,"],
)
def test_hallucinations_are_dropped(text: str) -> None:
    assert is_stt_hallucination(text)


@pytest.mark.parametrize(
    "text",
    [
        "Can you explain the Saga pattern?",
        "Thank you, now tell me about Kafka.",
        "What is a hash map?",
    ],
)
def test_real_speech_is_kept(text: str) -> None:
    assert not is_stt_hallucination(text)


@pytest.mark.asyncio
async def test_request_carries_vocab_prompt_and_language() -> None:
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"text": "Explain Kubernetes."}
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)

    provider = GroqWhisperSTTProvider(api_key="k", base_url="http://x", model="m")
    with patch("app.providers.stt.provider.shared_http", return_value=cm), patch(
        "app.providers.stt.provider.get_settings"
    ) as gs:
        gs.return_value.stt_language = "en"
        text = await provider.transcribe(b"RIFF" + b"0" * 60)

    assert text == "Explain Kubernetes."
    sent = client.post.call_args.kwargs["data"]
    assert sent["prompt"] == STT_PROMPT
    assert sent["language"] == "en"
