"""LLM provider abstraction."""
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from app.models.ai_response import AIInterviewResponse


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def analyze_question(
        self,
        resume: str,
        job_description: str,
        question: str,
        response_length: str = "medium",
        history: list[dict] | None = None,
    ) -> AIInterviewResponse:
        raise NotImplementedError

    async def analyze_question_stream(
        self,
        resume: str,
        job_description: str,
        question: str,
        response_length: str = "medium",
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        history: list[dict] | None = None,
    ) -> AIInterviewResponse:
        """Streaming variant: default is single-shot (no deltas).

        Providers with SSE support override this to emit one completed
        answer bullet per ``on_delta`` call while generating, then return
        the same strictly-validated response as :meth:`analyze_question`.
        """
        extra = {"history": history} if history else {}
        return await self.analyze_question(
            resume, job_description, question, response_length, **extra
        )