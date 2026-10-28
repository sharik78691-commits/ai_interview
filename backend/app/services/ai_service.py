"""Answer orchestration for one interview session.

Business logic only: provider choice goes through an injectable factory
(default: the model registry), so the core flow never names a concrete LLM
vendor and tests/alternative providers plug in without patching modules.
"""
import logging
import re
from collections.abc import Awaitable, Callable

from app.models.ai_response import AIInterviewResponse, FollowUp
from app.providers.llm.base import LLMProvider
from app.providers.llm.provider import LLMQuotaError, MockLLMProvider, is_rate_limited
from app.providers.llm.registry import get_llm_provider

logger = logging.getLogger(__name__)

#: Resolves a model key (None = default model) to a provider instance.
ProviderFactory = Callable[[str | None], LLMProvider]
#: Second model to try when the first is rate-limited/unavailable (None = none).
FallbackFactory = Callable[[str | None], LLMProvider | None]


def admin_model_only(model_key: str | None) -> LLMProvider | None:
    """No second model: answers come ONLY from the admin-selected model.

    A rate-limited / failing model keeps its own retries and then falls back
    to template guidance with a warning — it never switches silently to
    another provider (e.g. Gemini) the admin did not choose.
    """
    return None


class AIService:
    #: Shared default when no explicit length is provided by the client.
    default_length: str = "medium"

    def __init__(
        self,
        resume_text: str = "",
        job_description: str = "",
        response_length: str = "medium",
        provider_factory: ProviderFactory | None = None,
        fallback_factory: FallbackFactory | None = None,
    ) -> None:
        self.resume_text = resume_text or ""
        self.job_description = job_description or ""
        self.response_length = response_length or "medium"
        # None -> look up the module-level registry function at call time
        # (keeps existing monkeypatch-based tests working).
        self._provider_factory = provider_factory
        self._fallback_factory = fallback_factory or admin_model_only
        #: Set when the answer came from the fallback model:
        #: {"provider", "model", "usage"} of the model that failed first.
        self.last_fallback: dict | None = None
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
        # Full Best Answer per recent question (normalised) so on-demand
        # follow-ups build on what was actually advised, plus a per
        # (question, depth) cache so a second click never re-calls the model.
        self._answers: dict[str, list[str]] = {}
        self._follow_ups: dict[tuple[str, str], list[FollowUp]] = {}

    async def analyze(
        self,
        question: str,
        response_length: str | None = None,
        model_key: str | None = None,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> AIInterviewResponse:
        factory = self._provider_factory or get_llm_provider
        provider = factory(model_key)
        fallback = self._usable_fallback(model_key)
        if (
            fallback is not None
            and is_rate_limited(getattr(provider, "name", ""))
            and not is_rate_limited(getattr(fallback, "name", ""))
        ):
            # Still inside its per-minute window: a request would only earn
            # another 429. The fallback answers straight away, streamed.
            logger.info(
                "Answering with %s while %s is rate-limited",
                getattr(fallback, "name", "?"), getattr(provider, "name", "?"),
            )
            provider, fallback = fallback, None
        elif fallback is not None:
            # Another model can answer: a 429 must not be retried into the
            # same window first.
            provider.fail_fast_rate_limit = True
        level = response_length or self.response_length
        self.response_length = level
        self.last_warning = None
        self.last_fallback = None
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
            result = None
            failure = exc
            streamed = on_delta is not None and getattr(provider, "stream_had_deltas", False)
            if streamed:
                # Provisional bullets are already on screen: make ONE
                # single-shot attempt at the real answer before templates.
                try:
                    result = await provider.analyze_question(
                        self.resume_text, self.job_description, question, level, **extra
                    )
                except LLMQuotaError as exc2:
                    failure = exc2
            if result is None:
                # The model is rate-limited / out of quota / unreachable. A
                # per-minute limit does not clear within the short retries, so
                # ask another configured model once before falling back to
                # template guidance. Nothing is on screen yet (no deltas), so
                # its answer streams too instead of arriving all at once.
                result = await self._answer_with_fallback(
                    provider, fallback, question, level, extra,
                    on_delta=None if streamed else on_delta,
                )
            if result is None:
                # Live AI is unusable right now — keep the interview flowing with
                # template guidance, but tell the user why.
                self.last_warning = str(failure)
                mock = MockLLMProvider()
                result = await mock.analyze_question(
                    self.resume_text, self.job_description, question, level
                )
        if self.last_fallback is not None:
            self._remember(question, result)
            return result
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

    def _usable_fallback(self, model_key: str | None) -> LLMProvider | None:
        """Another real model that can answer if this one is rate-limited."""
        try:
            fallback = self._fallback_factory(model_key)
        except Exception:
            return None
        if fallback is None or isinstance(fallback, MockLLMProvider):
            return None
        return fallback

    async def _answer_with_fallback(
        self,
        failed: LLMProvider,
        fallback: LLMProvider | None,
        question: str,
        level: str,
        extra: dict,
        on_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> AIInterviewResponse | None:
        """One answer from another model (streamed when ``on_delta`` is given);
        None when there is none or it fails too. Usage of BOTH models is kept
        for telemetry."""
        if fallback is None:
            return None
        failed_usage = {
            "latency_ms": getattr(failed, "last_latency_ms", None),
            "input_tokens": getattr(failed, "last_input_tokens", None),
            "output_tokens": getattr(failed, "last_output_tokens", None),
            "error_category": getattr(failed, "last_error_category", None),
        }
        try:
            if on_delta is not None:
                result = await fallback.analyze_question_stream(
                    self.resume_text, self.job_description, question, level, on_delta, **extra
                )
            else:
                result = await fallback.analyze_question(
                    self.resume_text, self.job_description, question, level, **extra
                )
        except LLMQuotaError:
            return None
        self.last_fallback = {
            "provider": self.last_provider,
            "model": self.last_model,
            "usage": failed_usage,
        }
        self.last_provider = getattr(fallback, "name", "") or type(fallback).__name__
        self.last_model = getattr(fallback, "model", "") or ""
        self.last_usage = {
            "latency_ms": getattr(fallback, "last_latency_ms", None),
            "first_delta_ms": getattr(fallback, "first_delta_ms", None) if on_delta else None,
            "input_tokens": getattr(fallback, "last_input_tokens", None),
            "output_tokens": getattr(fallback, "last_output_tokens", None),
            "error_category": getattr(fallback, "last_error_category", None),
        }
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
        for q in {question, resolved}:
            self._answers[_norm(q)] = list(result.answerPoints)
        while len(self._answers) > self.ANSWERS_KEPT:
            self._answers.pop(next(iter(self._answers)))

    #: Recent answers kept for on-demand follow-ups.
    ANSWERS_KEPT = 20

    async def follow_ups(
        self, question: str, model_key: str | None = None
    ) -> list[FollowUp]:
        """On-demand "If they ask further" for an answered question.

        Same model, resume and JD as the answer; the Best Answer is passed as
        context. Cached per (question, depth). Raises LLMQuotaError when the
        provider is unavailable, so the caller can offer a retry.
        """
        level = self.response_length
        key = (_norm(question), level)
        if key in self._follow_ups:
            self.last_usage = {}
            return self._follow_ups[key]
        factory = self._provider_factory or get_llm_provider
        provider = factory(model_key)
        self.last_provider = getattr(provider, "name", "") or type(provider).__name__
        self.last_model = getattr(provider, "model", "") or ""
        items = await provider.generate_follow_ups(
            self.resume_text,
            self.job_description,
            question,
            self._answers.get(_norm(question), []),
            level,
        )
        self.last_usage = {
            "latency_ms": getattr(provider, "last_latency_ms", None),
            "input_tokens": getattr(provider, "last_input_tokens", None),
            "output_tokens": getattr(provider, "last_output_tokens", None),
            "error_category": getattr(provider, "last_error_category", None),
        }
        if items:
            self._follow_ups[key] = items
            while len(self._follow_ups) > self.ANSWERS_KEPT:
                self._follow_ups.pop(next(iter(self._follow_ups)))
        return items

    async def demo_guidance(self, question: str) -> AIInterviewResponse:
        provider = MockLLMProvider()
        return await provider.analyze_question(
            self.resume_text, self.job_description, question, self.response_length
        )


def _norm(question: str) -> str:
    return re.sub(r"\s+", " ", (question or "").strip().lower())
