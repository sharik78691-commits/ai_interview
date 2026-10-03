"""Debounced question detection over streaming transcript fragments."""
import time

QUESTION_WORDS = (
    "who", "what", "when", "where", "why", "how",
    "can", "could", "would", "should", "is", "are", "do", "does",
    "did", "have", "has", "will", "explain", "describe", "tell",
)


def is_question(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    words = t.split()
    if t.endswith("?"):
        return len(words) >= 3
    first = words[0].lower().strip("',\"")
    if first in QUESTION_WORDS and len(t) > 20 and len(words) >= 4:
        return True
    return False


class QuestionBuffer:
    """Accumulate fragments; emit a question once it looks complete."""

    def __init__(self, silence_ms: int = 1200) -> None:
        self.silence_ms = silence_ms
        self._buffer: str = ""
        self._last_push: float = 0.0

    def push(self, fragment: str) -> str | None:
        fragment = (fragment or "").strip()
        if not fragment:
            return None
        if self._buffer and not self._buffer.endswith((" ", "\n")):
            self._buffer += " "
        self._buffer += fragment
        self._last_push = time.monotonic()
        if is_question(self._buffer):
            return self.force_flush()
        return None

    def force_flush(self) -> str | None:
        text = self._buffer.strip()
        self._buffer = ""
        self._last_push = 0.0
        return text or None

    def push_complete(self, text: str) -> str | None:
        """Handle one finished utterance (e.g. a speech-to-text segment).

        Speech-to-text providers already cut clips at pauses, so a segment is a
        natural "the speaker stopped talking" boundary. Because of that we do NOT
        wait for extra fragments here.

        ANY substantial utterance triggers the AI — not just sentences ending in
        "?" or starting with a question word. Real interviewers say things like
        "give me a brief about Spring" or "tell me about your project" (no
        question mark, imperative form). A 50-line prompt is also just sent as-is;
        speed comes from the bounded output tokens, not from gating the input.
        """
        self._buffer = ""
        self._last_push = 0.0
        candidate = (text or "").strip()
        if not candidate:
            return None
        words = candidate.split()
        if len(words) < 3:
            return None
        # Clear question signal: "?" or leading question word.
        if candidate.endswith("?"):
            return candidate
        first = words[0].lower().strip("',\"")
        if first in QUESTION_WORDS:
            return candidate
        # Otherwise: any substantial statement is still an interviewer prompt.
        # Threshold keeps backchannels ("okay", "thank you", "nice nice") out
        # while letting real prompts through immediately.
        if len(words) >= 4 or len(candidate) >= 25:
            return candidate
        return None

    @property
    def pending(self) -> str:
        return self._buffer


class QuestionDetector:
    """Join transcript fragments and detect a trailing question."""

    def detect(self, transcript: str | list[str]) -> str | None:
        if isinstance(transcript, list):
            text = " ".join(t.strip() for t in transcript if t and t.strip())
        else:
            text = (transcript or "").strip()
        if is_question(text):
            return text
        return None
