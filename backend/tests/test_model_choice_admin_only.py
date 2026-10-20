"""Only admins choose the answering model; users always get the admin default.

A regular user's ``model`` field (REST body or WebSocket message) is ignored,
so the frontend's settings picker is cosmetic for them.
"""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.ai_response import AIInterviewResponse
from app.models.user import User
from app.providers.llm import registry
from tests.conftest import authenticate, csrf_headers, register

client = TestClient(app)


def _promote_to_admin(email: str) -> None:
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        u.is_admin = True
        db.commit()
    finally:
        db.close()


def _analyze(model: str):
    return client.post(
        "/api/interview/analyze",
        json={"question": "What is REST?", "model": model},
        headers=csrf_headers(client),
    )


def test_user_model_choice_is_ignored_over_rest() -> None:
    authenticate(client, "pick-user@example.com")
    with patch(
        "app.api.interview.validate_model_key", wraps=registry.validate_model_key
    ) as validate:
        r = _analyze("groq-gpt-oss")
    assert r.status_code == 200
    validate.assert_called_once_with(None)


def test_user_unknown_model_is_ignored_not_rejected() -> None:
    authenticate(client, "pick-user2@example.com")
    assert _analyze("evil-model").status_code == 200


def test_admin_model_choice_is_respected_over_rest() -> None:
    authenticate(client, "pick-admin@example.com")
    _promote_to_admin("pick-admin@example.com")
    with patch(
        "app.api.interview.validate_model_key", wraps=registry.validate_model_key
    ) as validate:
        r = _analyze("groq-gpt-oss")
    assert r.status_code == 200
    validate.assert_called_once_with("groq-gpt-oss")


def _fake_guidance() -> AIInterviewResponse:
    return AIInterviewResponse(
        question="Can you explain the Saga pattern?",
        questionType="technical",
        answerPoints=["Define Saga", "Explain compensation"],
        followUpQuestions=[],
        responseLength="medium",
    )


def _ws_model_used(email: str, admin: bool, ai_cls) -> object:
    ai_cls.return_value.analyze = AsyncMock(return_value=_fake_guidance())
    ai_cls.return_value.last_warning = None
    with patch("app.main.app"), TestClient(app) as ws_client:
        register(ws_client, email)
        if admin:
            _promote_to_admin(email)
        with ws_client.websocket_connect("/ws/interview") as ws:
            ws.receive_json()  # handshake
            ws.send_json(
                {"type": "force", "text": "Can you explain the Saga pattern?", "model": "groq-gpt-oss"}
            )
            for _ in range(8):
                if ws.receive_json().get("type") == "ai_guidance":
                    break
            else:
                raise AssertionError("no ai_guidance received")
    return ai_cls.return_value.analyze.await_args.kwargs["model_key"]


@patch("app.api.websocket.AIService")
def test_user_model_choice_is_ignored_over_ws(ai_cls) -> None:
    # None = the admin default, resolved at answer time.
    assert _ws_model_used("ws-pick-user@example.com", False, ai_cls) is None


@patch("app.api.websocket.AIService")
def test_admin_model_choice_is_respected_over_ws(ai_cls) -> None:
    assert _ws_model_used("ws-pick-admin@example.com", True, ai_cls) == "groq-gpt-oss"
