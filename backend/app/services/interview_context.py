"""Per-user interview context (resume, job description, answer length).

One store shared by the REST interview routes, the live WebSocket and logout.
It used to be a module-level dict inside ``app.api.interview``, so the auth
and WebSocket routers had to import another router to reach it. As a service
it has a single owner and one obvious place to swap in Redis when the API runs
on more than one worker.

Deliberately in memory: interview context is never persisted (privacy).
"""
from typing import TypedDict

#: Accepted answer depths. "default" lets the model choose.
VALID_LENGTHS: frozenset[str] = frozenset({"default", "short", "medium", "long"})

_LENGTH_ALIASES = {
    "concise": "short",
    "brief": "short",
    "standard": "medium",
    "detailed": "long",
}


class InterviewContext(TypedDict):
    resume: str
    job: str
    length: str


def normalize_length(value: str | None) -> str:
    """Map UI/legacy aliases onto a canonical length. Empty means ``medium``.

    Unknown values are returned as-is; callers check ``VALID_LENGTHS``.
    """
    v = (value or "").strip().lower()
    return _LENGTH_ALIASES.get(v, v) if v else "medium"


def coerce_length(value: str | None) -> str:
    """``normalize_length`` and fall back to ``medium`` for unknown values."""
    length = normalize_length(value)
    return length if length in VALID_LENGTHS else "medium"


class InterviewContextStore:
    """In-memory context buckets keyed by the authenticated user id.

    Keys always come from the server-side session, never from the client,
    so one user can never read another user's resume or job description.
    """

    def __init__(self) -> None:
        self._contexts: dict[int, InterviewContext] = {}

    def get(self, user_id: int) -> InterviewContext:
        """Return (creating if needed) the live, mutable bucket for one user."""
        return self._contexts.setdefault(
            user_id, {"resume": "", "job": "", "length": "medium"}
        )

    def update(self, user_id: int, *, resume: str, job: str, length: str) -> InterviewContext:
        ctx = self.get(user_id)
        ctx["resume"] = resume
        ctx["job"] = job
        ctx["length"] = length
        return ctx

    def clear(self, user_id: int) -> None:
        self._contexts.pop(user_id, None)

    def __contains__(self, user_id: object) -> bool:
        return user_id in self._contexts


#: Process-wide default store.
context_store = InterviewContextStore()

__all__ = [
    "InterviewContext",
    "InterviewContextStore",
    "VALID_LENGTHS",
    "coerce_length",
    "context_store",
    "normalize_length",
]
