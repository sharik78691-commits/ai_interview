"""Follow-up handling: the model must see prior turns and the strong-answer rules."""
import pytest

from app.models.ai_response import AIInterviewResponse
from app.providers.llm.provider import (
    build_system_prompt,
    format_history,
)
from app.services.ai_service import AIService


def _resp(q: str, pts: list[str]) -> AIInterviewResponse:
    return AIInterviewResponse(
        question=q, questionType="technical", answerPoints=pts, responseLength="medium"
    )


class _Recorder:
    name = "rec"
    model = "rec"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def analyze_question(self, resume, job, question, level="medium", history=None):
        self.calls.append({"question": question, "history": history})
        resolved = "Why did you pick Kafka over RabbitMQ?" if question == "Why?" else question
        return _resp(resolved, [f"point for {resolved}"])


def test_prompt_has_answer_shape_and_followup_rules() -> None:
    p = build_system_prompt("medium")
    assert "DIRECT answer" in p
    assert "FOLLOW-UPS" in p
    assert "self-contained question" in p


def test_format_history_empty_and_filled() -> None:
    assert format_history(None) == ""
    block = format_history([{"question": "What is Kafka?", "points": ["A log", "Partitions"]}])
    assert "Q1: What is Kafka?" in block and "Advised: A log | Partitions" in block


@pytest.mark.asyncio
async def test_followup_receives_history_and_stores_resolved_question(monkeypatch) -> None:
    rec = _Recorder()
    monkeypatch.setattr("app.services.ai_service.get_llm_provider", lambda *_: rec)
    svc = AIService("resume", "job")

    await svc.analyze("What is Kafka?")
    assert rec.calls[0]["history"] is None  # first question: nothing extra sent

    await svc.analyze("Why?")
    assert rec.calls[1]["history"][0]["question"] == "What is Kafka?"
    # The bare "Why?" is remembered as the full question the model resolved.
    assert svc.history[-1]["question"] == "Why did you pick Kafka over RabbitMQ?"


@pytest.mark.asyncio
async def test_history_is_capped(monkeypatch) -> None:
    rec = _Recorder()
    monkeypatch.setattr("app.services.ai_service.get_llm_provider", lambda *_: rec)
    svc = AIService("r", "j")
    for i in range(7):
        await svc.analyze(f"Question number {i}?")
    assert len(svc.history) == AIService.HISTORY_TURNS
