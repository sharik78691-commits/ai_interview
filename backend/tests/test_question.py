"""Question detection tests."""
from app.services.question_service import QuestionBuffer, QuestionDetector, is_question


def test_is_question_true() -> None:
    assert is_question("Can you explain event delegation in JavaScript?")
    assert is_question("Tell me about a time you resolved a team conflict at work?")
    assert is_question("How do you optimize a slow SQL query in production systems?")
    assert is_question("What is Java?")


def test_short_question_needs_force_path() -> None:
    # "what is Java" (no "?") is intentionally NOT auto-fired from fragments —
    # the client detects it and triggers analysis via the force path instead.
    assert not is_question("what is Java")
    buf = QuestionBuffer()
    assert buf.push("what is Java") is None  # buffered, not auto-fired
    assert buf.force_flush() == "what is Java"  # force analyzes it anyway


def test_is_question_false() -> None:
    assert not is_question("")
    assert not is_question("yeah okay")
    assert not is_question("The weather is nice today and we shipped the release")


def test_buffer_join() -> None:
    buf = QuestionBuffer()
    assert buf.push("Can you explain") is None
    result = buf.push("event delegation in JavaScript?")
    assert result is not None
    assert "event delegation" in result


def test_detector() -> None:
    det = QuestionDetector()
    assert det.detect(["Can you explain", "closures in JavaScript?"]) is not None
    assert det.detect("just a statement about work") is None
