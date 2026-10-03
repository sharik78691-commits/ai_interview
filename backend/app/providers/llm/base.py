"""LLM provider abstraction."""
from abc import ABC, abstractmethod

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
    ) -> AIInterviewResponse:
        raise NotImplementedError