"""LLM token-streaming tests (mocked SSE — no external API calls)."""
import json
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.providers.llm.provider import (
    LLMQuotaError,
    OpenAICompatibleLLMProvider,
    _extract_answer_points,
)
from tests.test_llm_registry import GUIDANCE

client = TestClient(app)


def _sse_lines(payload: dict,halves: int = 2) -> list[str]:
    """Split a full JSON body into ``halves`` SSE data chunks (mid-string cuts
    allowed — the extractor must handle them)."""
    full = json.dumps(payload)
    size = len(full) // halves
    parts = [full[i * size:(i + 1) * size] for i in range(halves - 1)]
    parts.append(full[(halves - 1) * size:])
    return [
        "data: " + json.dumps({"choices": [{"delta": {"content": p}}]})
        for p in parts
    ] + ["data: [DONE]"]


class _FakeStreamResp:
    def __init__(self, status_code: int = 200, lines: list | None = None):
        self.status_code = status_code
        self._lines = lines or []
        self.request = None
        self.headers: dict = {}
        self.text = ""

    async def aread(self) -> bytes:
        return b""

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln


def _stream_client_for(resp: _FakeStreamResp):
    class _StreamCM:
        async def __aenter__(self):
            return resp

        async def __aexit__(self, *args):
            return False

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return _StreamCM()

    return _Client()


def _provider(**kwargs) -> OpenAICompatibleLLMProvider:
    return OpenAICompatibleLLMProvider(
        api_key="test-key", base_url="https://example.test/v1", model="test-model",
        **kwargs,
    )


# ------------------------------------------------------------ point extractor

def test_extract_answer_points_incremental() -> None:
    full = json.dumps(GUIDANCE)
    cut = len(full) // 2
    first = _extract_answer_points(full[:cut])
    both = _extract_answer_points(full)
    # Every point of the full doc is a complete string; the half yields a
    # prefix of them (possibly empty) — never garbage.
    assert both == GUIDANCE["answerPoints"]
    assert first == both[: len(first)]
    assert _extract_answer_points('{"other": 1}') == []
    assert _extract_answer_points('{"answerPoints": [') == []


# ------------------------------------------------------------ provider stream

async def test_stream_emits_deltas_then_validated_response() -> None:
    provider = _provider()
    deltas: list[str] = []
    fake = _stream_client_for(_FakeStreamResp(200, _sse_lines(GUIDANCE)))
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        resp = await provider.analyze_question_stream(
            "r", "j", "What is a closure?", "medium", _collect(deltas),
        )
    assert resp.question == "What is a closure?"
    assert deltas == GUIDANCE["answerPoints"]
    assert provider.stream_had_deltas is True
    assert provider.last_error_category == "ok"


def _collect(deltas: list[str]):
    async def _cb(point: str) -> None:
        deltas.append(point)

    return _cb


async def test_stream_pre_delta_429_raises_without_deltas() -> None:
    provider = _provider()
    provider.max_retries = 0
    seen: list[str] = []
    fake = _stream_client_for(_FakeStreamResp(429, []))
    with patch("app.providers.llm.provider.shared_http", return_value=fake):
        try:
            await provider.analyze_question_stream(
                "r", "j", "What is REST?", "medium", _collect(seen)
            )
            raise AssertionError("expected LLMQuotaError")
        except LLMQuotaError as exc:
            assert "rate limit" in str(exc).lower()
    assert seen == []
    assert provider.stream_had_deltas is False
    assert provider.last_error_category == "rate_limit"


# ------------------------------------------------------------ service fallback

async def test_service_singleshot_retries_after_midstream_failure() -> None:
    """Deltas already shown -> one single-shot attempt before templates."""
    from app.models.ai_response import AIInterviewResponse
    from app.services import ai_service as svc_mod

    real = AIInterviewResponse(**GUIDANCE)

    class _Stub:
        name = "stub"
        model = "stub-model"
        stream_had_deltas = True

        async def analyze_question_stream(self, *args, **kwargs):
            raise LLMQuotaError("mid-stream drop")

        async def analyze_question(self, *args, **kwargs):
            return real

    with patch.object(svc_mod, "get_llm_provider", return_value=_Stub()):
        svc = svc_mod.AIService(resume_text="r", job_description="j")
        out = await svc.analyze("q", on_delta=AsyncMock())
    assert out.question == GUIDANCE["question"]
    assert svc.last_warning is None


async def test_service_prefailure_streams_use_templates() -> None:
    """No deltas yet -> same mock+warning path as single-shot."""
    from app.services import ai_service as svc_mod

    class _Stub:
        name = "stub"
        model = "stub-model"
        stream_had_deltas = False
        last_latency_ms = 1.0
        last_input_tokens = None
        last_output_tokens = None
        last_error_category = "rate_limit"

        async def analyze_question_stream(self, *args, **kwargs):
            raise LLMQuotaError("LLM rate limit reached")

    with patch.object(svc_mod, "get_llm_provider", return_value=_Stub()):
        svc = svc_mod.AIService(resume_text="r", job_description="j")
        out = await svc.analyze("q", on_delta=AsyncMock())
    assert out.answerPoints
    assert svc.last_warning and "rate limit" in svc.last_warning.lower()


# ------------------------------------------------------------ websocket frames

def _auth_ws(client: TestClient) -> None:
    client.post(
        "/api/auth/register",
        json={"email": "ws-stream@example.com", "password": "Passw0rd123", "name": "WS"},
    )


@patch("app.api.websocket.AIService")
def test_ws_streams_start_deltas_then_guidance(ai_cls) -> None:
    from app.models.ai_response import AIInterviewResponse

    async def fake_analyze(question, model_key=None, on_delta=None, **kwargs):
        if on_delta is not None:
            await on_delta("Define scope capture.")
            await on_delta("Give an example.")
        return AIInterviewResponse(**GUIDANCE)

    ai_cls.return_value.analyze = AsyncMock(side_effect=fake_analyze)
    ai_cls.return_value.last_warning = None

    with patch("app.main.app"), TestClient(app) as tc:
        _auth_ws(tc)
        with tc.websocket_connect("/ws/interview") as ws:
            ws.receive_json()  # handshake
            ws.send_json(
                {"type": "interviewer_question", "text": "What is a closure?"}
            )
            # interviewer_question echoes stt_transcript + status first.
            frames = [ws.receive_json() for _ in range(6)]

    assert frames[0]["type"] == "stt_transcript", frames
    assert frames[1]["type"] == "status", frames
    assert frames[2]["type"] == "ai_stream_start", frames
    assert frames[2]["model"] == "groq-gpt-oss", frames
    assert frames[3] == {"type": "ai_stream_delta", "point": "Define scope capture."}
    assert frames[4] == {"type": "ai_stream_delta", "point": "Give an example."}
    assert frames[5]["type"] == "ai_guidance", frames
    assert frames[5]["data"]["answerPoints"]
    assert ai_cls.return_value.analyze.await_count == 1
