"""AI interview session service (in-memory context store)."""
import uuid
from collections.abc import Awaitable, Callable

from app.models.ai_response import AIInterviewResponse
from app.providers.llm.registry import get_llm_provider
from app.providers.llm.provider import LLMQuotaError, MockLLMProvider


class AIService:
    sessions: dict[str, dict[str, str]] = {}

    #: Shared default when no explicit length is provided by the client.
    default_length: str = "medium"

    def __init__(
        self,
        resume_text: str = "",
        job_description: str = "",
        response_length: str = "medium",
    ) -> None:
        self.resume_text = resume_text or ""
        self.job_description = job_description or ""
        self.response_length = response_length or "medium"
        self.session_id = str(uuid.uuid4())
        AIService.sessions[self.session_id] = {
            "resume": self.resume_text,
            "job": self.job_description,
            "length": self.response_length,
        }
        # User-facing notice for the most recent call (None when all good).
        self.last_warning: str | None = None
        # Last provider/model actually used (for usage tracking + UI display).
        self.last_provider: str = ""
        self.last_model: str = ""
        # Usage telemetry dict captured after analyze(): latency/tokens/error.
        self.last_usage: dict = {}

    async def analyze(
        self,
        question: str,
        response_length: str | None = None,
        model_key: str | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> AIInterviewResponse:
        provider = get_llm_provider(model_key)
        level = response_length or self.response_length
        self.response_length = level
        self.last_warning = None
        self.last_provider = getattr(provider, "name", "") or type(provider).__name__
        self.last_model = getattr(provider, "model", "") or ""
        try:
            if on_delta is not None:
                result = await provider.analyze_question_stream(
                    self.resume_text, self.job_description, question, level, on_delta
                )
            else:
                result = await provider.analyze_question(
                    self.resume_text, self.job_description, question, level
                )
        except LLMQuotaError as exc:
            if on_delta is not None and getattr(
                provider, "stream_had_deltas", False
            ):
                # Provisional bullets are already on screen: make ONE
                # single-shot attempt at the real answer before templates.
                try:
                    result = await provider.analyze_question(
                        self.resume_text, self.job_description, question, level
                    )
                except LLMQuotaError as exc2:
                    self.last_warning = str(exc2)
                    mock = MockLLMProvider()
                    result = await mock.analyze_question(
                        self.resume_text, self.job_description, question, level
                    )
            else:
                # Live AI is unusable right now — keep the interview flowing with
                # template guidance, but tell the user why.
                self.last_warning = str(exc)
                mock = MockLLMProvider()
                result = await mock.analyze_question(
                    self.resume_text, self.job_description, question, level
                )
        # Usage telemetry from the LIVE provider (never the mock fallback).
        self.last_usage = {
            "latency_ms": getattr(provider, "last_latency_ms", None),
            "input_tokens": getattr(provider, "last_input_tokens", None),
            "output_tokens": getattr(provider, "last_output_tokens", None),
            "error_category": getattr(provider, "last_error_category", None),
        }
        return result

    async def demo_guidance(self, question: str) -> AIInterviewResponse:
        provider = MockLLMProvider()
        return await provider.analyze_question(
            self.resume_text, self.job_description, question, self.response_length
        )

    @classmethod
    def get_session(cls, session_id: str) -> dict[str, str] | None:
        return cls.sessions.get(session_id)