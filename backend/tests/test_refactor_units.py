"""Unit tests for the decoupling refactor: context store, owned-library
repository, model-config service and AIService provider injection."""
import pytest

from app.db.session import SessionLocal
from app.models.ai_response import AIInterviewResponse
from app.models.user import User


def _user(db, email: str) -> User:
    u = User(email=email, name="", auth_provider="local", is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


# ------------------------------------------------------------ context store


def test_context_store_isolates_users_and_clears():
    from app.services.interview_context import InterviewContextStore

    store = InterviewContextStore()
    store.update(1, resume="r1", job="j1", length="short")
    assert store.get(1) == {"resume": "r1", "job": "j1", "length": "short"}
    assert store.get(2) == {"resume": "", "job": "", "length": "medium"}
    store.clear(1)
    assert 1 not in store
    assert store.get(1)["resume"] == ""


@pytest.mark.parametrize(
    "raw,normalized,coerced",
    [(None, "medium", "medium"), ("concise", "short", "short"), ("DETAILED", "long", "long"),
     ("default", "default", "default"), ("weird", "weird", "medium")],
)
def test_length_helpers(raw, normalized, coerced):
    from app.services.interview_context import coerce_length, normalize_length

    assert normalize_length(raw) == normalized
    assert coerce_length(raw) == coerced


def test_router_compat_names_share_the_store():
    """Old imports from app.api.interview still see the same live data."""
    from app.api import interview
    from app.services.interview_context import context_store

    context_store.update(99, resume="x", job="y", length="long")
    try:
        assert interview.get_context(99)["resume"] == "x"
        assert 99 in interview._contexts
        interview.clear_context(99)
        assert 99 not in context_store
    finally:
        context_store.clear(99)


# ------------------------------------------------------- owned library repo


def test_owned_library_prunes_and_keeps_one_active():
    from app.models.target_role import TargetRole
    from app.repositories.owned_library import OwnedLibraryRepository

    repo = OwnedLibraryRepository(TargetRole, max_items=2, label="Role")
    db = SessionLocal()
    try:
        u = _user(db, "lib@example.com")
        other = _user(db, "other@example.com")
        for i in range(3):
            repo.add_active(db, TargetRole(user_id=u.id, user_email=u.email,
                                           position=f"P{i}", job_description="JD"))
        foreign = repo.add_active(db, TargetRole(user_id=other.id, user_email=other.email,
                                                 position="X", job_description="JD"))
        rows = repo.list_for_user(db, u.id)
        assert [r.position for r in rows] == ["P2", "P1"]          # oldest pruned
        assert [r.is_active for r in rows] == [True, False]        # newest active
        assert repo.get_owned(db, u.id, foreign.id) is None         # scoped by owner
        assert repo.get_owned(db, other.id, foreign.id).is_active   # untouched

        repo.set_active(db, rows[1], True)
        db.commit()
        assert [r.is_active for r in repo.list_for_user(db, u.id)] == [False, True]

        repo.delete(db, rows[1])                                    # delete active
        remaining = repo.list_for_user(db, u.id)
        assert len(remaining) == 1 and remaining[0].is_active       # promoted
    finally:
        db.close()


# ------------------------------------------------------ model config service


def test_model_update_default_scope_isolated_between_catalogues():
    from app.models.ai_model_config import AIModelConfig
    from app.schemas.admin import AIModelUpdateRequest
    from app.services.model_config_service import apply_model_update

    db = SessionLocal()
    try:
        apply_model_update(db, "llm-a", AIModelUpdateRequest(is_default=True), None,
                           allow_catalog_fields=True)
        apply_model_update(db, "stt-a", AIModelUpdateRequest(is_default=True), {"stt-a", "stt-b"},
                           allow_catalog_fields=False)
        # STT default must not clear the LLM default sharing the table.
        assert db.get(AIModelConfig, "llm-a").is_default is True
        apply_model_update(db, "stt-b", AIModelUpdateRequest(is_default=True), {"stt-a", "stt-b"},
                           allow_catalog_fields=False)
        db.expire_all()
        assert db.get(AIModelConfig, "stt-a").is_default is False
        assert db.get(AIModelConfig, "llm-a").is_default is True
        # Catalogue fields are LLM-only.
        rec = apply_model_update(db, "stt-b", AIModelUpdateRequest(priority=5000),
                                 {"stt-a", "stt-b"}, allow_catalog_fields=False)
        assert rec.priority != 5000
        rec = apply_model_update(db, "llm-a", AIModelUpdateRequest(priority=5000), None,
                                 allow_catalog_fields=True)
        assert rec.priority == 1000  # clamped
    finally:
        db.close()


# ---------------------------------------------------------- AIService DI


async def test_ai_service_uses_injected_provider_factory():
    from app.providers.llm.base import LLMProvider
    from app.services.ai_service import AIService

    seen: list[str | None] = []

    class Fake(LLMProvider):
        name = "fake"

        async def analyze_question(self, resume, job_description, question,
                                   response_length="medium", history=None):
            return AIInterviewResponse(question=question, questionType="general",
                                       answerPoints=["ok"], responseLength=response_length)

    def factory(key):
        seen.append(key)
        return Fake()

    svc = AIService("r", "j", provider_factory=factory)
    out = await svc.analyze("What is REST?", model_key="some-key")
    assert out.answerPoints == ["ok"]
    assert seen == ["some-key"]
    assert svc.last_provider == "fake"


def test_ai_service_no_longer_keeps_a_global_session_registry():
    from app.services.ai_service import AIService

    for _ in range(3):
        AIService("r", "j")
    assert not hasattr(AIService, "sessions")


# ------------------------------------------------- WebSocket normal close


def test_ws_normal_disconnect_is_not_logged_as_error(client, caplog):
    """A client closing the socket is routine — no ERROR/traceback noise."""
    import logging
    import time

    from tests.conftest import register

    register(client, "ws-close@example.com")
    with caplog.at_level(logging.INFO, logger="app.api.websocket"):
        with client.websocket_connect("/ws/interview") as ws:
            assert ws.receive_json()["type"] == "status"
        time.sleep(0.3)  # let the server loop observe the close
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR and r.name == "app.api.websocket"]
    assert not errors, [r.getMessage() for r in errors]
