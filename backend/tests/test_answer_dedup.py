"""Duplicate-answer suppression + backchannel gating on the live socket.

Covers three real pipeline bugs:
1. The mic path (sendTranscript) and the debounced client detector (sendForce)
   can report the SAME question twice -> exactly one AI call must happen.
2. Backchannels ("Okay?") must not spend an AI call.
3. Retry-after-failure must still work (failures are never deduped).

AIService is mocked; no network calls in these tests.
"""
from unittest.mock import AsyncMock, patch

from app.models.ai_response import AIInterviewResponse


def _auth(client) -> None:
    client.post(
        "/api/auth/register",
        json={"email": "ws-dedup@example.com", "password": "Passw0rd123", "name": "WS"},
    )


def _fake_guidance() -> AIInterviewResponse:
    return AIInterviewResponse(
        question="Can you explain the Saga pattern?",
        questionType="technical",
        answerPoints=["Define Saga", "Explain compensation"],
        followUpQuestions=["Choreography vs orchestration?"],
        responseLength="medium",
    )


def _mocked_ai(ai_cls, analyze=None) -> None:
    ai_cls.return_value.analyze = analyze or AsyncMock(return_value=_fake_guidance())
    ai_cls.return_value.last_warning = None


def _recv_until(ws, want: str, limit: int = 8) -> dict:
    """Read frames until a message of type ``want`` arrives (or fail)."""
    for _ in range(limit):
        msg = ws.receive_json()
        if msg.get("type") == want:
            return msg
    raise AssertionError(f"never received message type {want!r}")


class TestDuplicateSuppression:
    @patch("app.api.websocket.AIService")
    def test_same_question_twice_yields_one_answer(self, ai_cls) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        _mocked_ai(ai_cls)

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()  # handshake
                # Mic fragment completes a question -> server fires immediately.
                ws.send_json(
                    {"type": "transcript", "text": "Can you explain the Saga pattern?"}
                )
                first = _recv_until(ws, "ai_guidance")
                assert first["data"]["answerPoints"]

                # Debounced client detector reports the same question again.
                ws.send_json(
                    {"type": "force", "text": "Can you explain the Saga pattern?"}
                )
                dup = ws.receive_json()
                assert dup["type"] == "status", dup
                assert "Already answered" in dup.get("message", ""), dup

        assert ai_cls.return_value.analyze.await_count == 1

    @patch("app.api.websocket.AIService")
    def test_length_change_reattempts_even_within_window(self, ai_cls) -> None:
        """A length change is a deliberate re-ask, never a duplicate."""
        from fastapi.testclient import TestClient

        from app.main import app

        _mocked_ai(ai_cls)

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()  # handshake
                ws.send_json(
                    {
                        "type": "force",
                        "text": "Can you explain the Saga pattern?",
                        "responseLength": "medium",
                    }
                )
                first = _recv_until(ws, "ai_guidance")
                assert first["data"]["answerPoints"]

                # Same question, DIFFERENT depth -> must NOT be suppressed.
                ws.send_json(
                    {
                        "type": "force",
                        "text": "Can you explain the Saga pattern?",
                        "responseLength": "long",
                    }
                )
                second = _recv_until(ws, "ai_guidance")
                assert second["data"]["answerPoints"]

        assert ai_cls.return_value.analyze.await_count == 2

    @patch("app.api.websocket.AIService")
    def test_backchannel_question_mark_gets_no_answer(self, ai_cls) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        _mocked_ai(ai_cls)

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()  # handshake
                ws.send_json({"type": "transcript", "text": "Okay?"})
                msg = ws.receive_json()
                assert msg["type"] == "status", msg

        ai_cls.return_value.analyze.assert_not_awaited()

    @patch("app.api.websocket.AIService")
    def test_retry_after_failure_goes_through(self, ai_cls) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        _mocked_ai(
            ai_cls,
            analyze=AsyncMock(
                side_effect=[RuntimeError("boom"), _fake_guidance()]
            ),
        )

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()  # handshake
                ws.send_json(
                    {"type": "force", "text": "Can you explain the Saga pattern?"}
                )
                err = _recv_until(ws, "error")
                assert "unavailable" in err["message"]

                # Same question retried -> must reach the AI again.
                ws.send_json(
                    {"type": "force", "text": "Can you explain the Saga pattern?"}
                )
                second = _recv_until(ws, "ai_guidance")
                assert second["data"]["answerPoints"]

        assert ai_cls.return_value.analyze.await_count == 2

    @patch("app.api.websocket.AIService")
    def test_long_question_answered_once_with_full_text(self, ai_cls) -> None:
        """A long question arriving in pieces (600ms pauses, no "?") must
        NOT fire halfway — one AI call with the COMPLETE text."""
        from fastapi.testclient import TestClient

        from app.main import app

        _mocked_ai(ai_cls)

        part1 = "Can you walk me through how you would design a rate limiter"
        part2 = "for a high throughput API gateway handling millions of requests"

        with patch("app.main.app"), TestClient(app) as client:
            _auth(client)
            with client.websocket_connect("/ws/interview") as ws:
                ws.receive_json()  # handshake
                # Two fragments, neither fires (no "?").
                ws.send_json({"type": "transcript", "text": part1})
                assert ws.receive_json()["type"] == "status"
                ws.send_json({"type": "transcript", "text": part2})
                assert ws.receive_json()["type"] == "status"
                ai_cls.return_value.analyze.assert_not_awaited()

                # Pause detector reaps the complete question via force.
                ws.send_json(
                    {
                        "type": "force",
                        "text": f"{part1} {part2}",
                    }
                )
                guidance = _recv_until(ws, "ai_guidance")
                assert guidance["data"]["answerPoints"]

        assert ai_cls.return_value.analyze.await_count == 1
        asked = ai_cls.return_value.analyze.await_args[0][0]
        assert asked.startswith("Can you walk me")
        assert asked.endswith("requests")
