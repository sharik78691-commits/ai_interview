"""LLM provider abstraction."""
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from app.models.ai_response import AIInterviewResponse, FollowUp


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

    async def generate_follow_ups(
        self,
        resume: str,
        job_description: str,
        question: str,
        answer_points: list[str],
        response_length: str = "medium",
    ) -> list[FollowUp]:
        """On-demand "If they ask further" items for an answered question.

        Answers no longer carry follow-ups (they were 15-37% of every answer's
        output); the candidate requests them with a button. Default: none.
        """
        return []

    async def complete_json(self, system: str, user: str, max_tokens: int = 1200) -> dict:
        """One free-form JSON request (mock-interview questions / feedback).

        Providers that can call a real model override this. The default has
        no model behind it, so callers fall back to their own templates.
        """
        raise NotImplementedError