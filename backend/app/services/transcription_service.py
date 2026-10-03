"""Transcription session registry (MVP: browser-side STT)."""


class TranscriptionService:
    """Tracks active websocket transcription sessions.

    Actual speech-to-text runs in the browser via the Web Speech API;
    the server only buffers transcript fragments per connection.
    """

    def __init__(self) -> None:
        self.sessions: dict[str, list[str]] = {}

    def start(self, session_id: str) -> None:
        self.sessions.setdefault(session_id, [])

    def append(self, session_id: str, text: str) -> None:
        self.sessions.setdefault(session_id, []).append(text)

    def get_transcript(self, session_id: str) -> str:
        return " ".join(self.sessions.get(session_id, []))

    def end(self, session_id: str) -> str:
        transcript = self.get_transcript(session_id)
        self.sessions.pop(session_id, None)
        return transcript
