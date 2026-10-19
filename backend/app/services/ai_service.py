"""Answer orchestration for one interview session.

Business logic only: provider choice goes through an injectable factory
(default: the model registry), so the core flow never names a concrete LLM
vendor and tests/alternative providers plug in without patching modules.
"""
from collections.abc import Awaitable, Callable

from app.models.ai_response import AIInterviewResponse
from app.providers.llm.base import LLMProvider
from app.providers.llm.provider import LLMQuotaError, MockLLMProvider
from app.providers.llm.registry import get_llm_provider

#: Resolves a model key (None = default model) to a provider instance.
ProviderFactory = Callable[[str | None], LLMProvider]


class AIService:
    #: Shared default when no explicit length is provided by the client.
    default_length: str = "medium"

    def __init__(
        self,
        resume_text: str = "",
        job_description: str = "",
        response_length: str = "medium",
        provider_factory: ProviderFactory | None = None,
    ) -> None:
        self.resume_text = resume_text or ""
        self.job_description = job_description or ""
        self.response_length = response_length or "medium"
        # None -> look up the module-level registry function at call time
        # (keeps existing monkeypatch-based tests working).
        self._provider_factory = provider_factory
        # User-facing notice for the most recent call (None when all good).
        self.last_warning: str | None = None
        # Last provider/model actually used (for usage tracking + UI display).
        self.last_provider: str = ""
        self.last_model: str = ""
        # Usage telemetry dict captured after analyze(): latency/tokens/error.
        self.last_usage: dict = {}
        # Recent Q&A turns, so a follow-up ("why?", "and at 10x scale?") is
        # answered in context instead of as a cold, unrelated question.
        self.history: list[dict] = []

    async def analyze(
        self,
        question: str,
        response_length: str | None = None,
        model_key: str | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> AIInterviewResponse:
        factory = self._provider_factory or get_llm_provider
        provider = factory(model_key)
        level = response_length or self.response_length
        self.response_length = level
        self.last_warning = None
        self.last_provider = getattr(provider, "name", "") or type(provider).__name__
        self.last_model = getattr(provider, "model", "") or ""
        # Passed only when there is history, so first questions (and providers
        # that predate the argument) behave exactly as before.
        extra = {"history": list(self.history)} if self.history else {}
        try:
            if on_delta is not None:
                result = await provider.analyze_question_stream(
                    self.resume_text, self.job_description, question, level, on_delta, **extra
                )
            else:
                result = await provider.analyze_question(
                    self.resume_text, self.job_description, question, level, **extra
                )
        except LLMQuotaError as exc:
            if on_delta is not None and getattr(
                provider, "stream_had_deltas", False
            ):
                # Provisional bullets are already on screen: make ONE
                # single-shot attempt at the real answer before templates.
                try:
                    result = await provider.analyze_question(
                        self.resume_text, self.job_description, question, level, **extra
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
            # Time to the FIRST provisional bullet. This is the number the
            # candidate actually perceives as response latency — the total above
            # hides the model's silent thinking phase. None on non-stream calls.
            "first_delta_ms": getattr(provider, "first_delta_ms", None),
            "input_tokens": getattr(provider, "last_input_tokens", None),
            "output_tokens": getattr(provider, "last_output_tokens", None),
            "error_category": getattr(provider, "last_error_category", None),
        }
        self._remember(question, result)
        return result

    #: Turns kept for follow-up context (older ones add tokens, not signal).
    HISTORY_TURNS = 4

    def _remember(self, question: str, result: AIInterviewResponse) -> None:
        """Record a turn. Uses the model's resolved question when it has one,
        so a bare "why?" is stored as the full question it really was."""
        if not isinstance(result, AIInterviewResponse):
            return  # history is best-effort; never break an answer over it
        resolved = (result.question or "").strip() or question
        self.history.append({"question": resolved, "points": result.answerPoints[:3]})
        del self.history[: -self.HISTORY_TURNS]

    async def demo_guidance(self, question: str) -> AIInterviewResponse:
        provider = MockLLMProvider()
        return await provider.analyze_question(
            self.resume_text, self.job_description, question, self.response_length
        )
