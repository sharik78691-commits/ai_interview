"""AI interview session service (in-memory context store)."""
import uuid

from app.models.ai_response import AIInterviewResponse
from app.providers.llm.provider import LLMQuotaError, MockLLMProvider, get_llm_provider


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

    async def analyze(
        self, question: str, response_length: str | None = None
    ) -> AIInterviewResponse:
        provider = get_llm_provider()
        level = response_length or self.response_length
        self.response_length = level
        self.last_warning = None
        try:
            return await provider.analyze_question(
                self.resume_text, self.job_description, question, level
            )
        except LLMQuotaError as exc:
            # Live AI is unusable right now — keep the interview flowing with
            # template guidance, but tell the user why.
            self.last_warning = str(exc)
            mock = MockLLMProvider()
            return await mock.analyze_question(
                self.resume_text, self.job_description, question, level
            )

    async def demo_guidance(self, question: str) -> AIInterviewResponse:
        provider = MockLLMProvider()
        return await provider.analyze_question(
            self.resume_text, self.job_description, question, self.response_length
        )

    @classmethod
    def get_session(cls, session_id: str) -> dict[str, str] | None:
        return cls.sessions.get(session_id)