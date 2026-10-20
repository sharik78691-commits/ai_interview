"""Shared test fixtures.

Uses an isolated in-memory SQLite database and a deterministic session secret
so tests never touch the developer's real database or .env values.
"""
import atexit
import os
import tempfile
from pathlib import Path

# A per-process SQLite file: two concurrent test runs (or a stuck earlier run)
# used to share ./test_auth.db and block each other on the SQLite lock.
_TEST_DB = Path(tempfile.gettempdir()) / f"aia_test_{os.getpid()}.db"


@atexit.register
def _remove_test_db() -> None:
    try:
        from app.db.session import engine as _engine

        _engine.dispose()  # release the SQLite handle (Windows file lock)
        _TEST_DB.unlink(missing_ok=True)
    except Exception:
        pass  # best effort: a leftover temp file is harmless

# Never call real (billed) AI/STT providers from the test suite: config.py
# loads backend/.env, which on a developer machine holds live keys. An empty
# value already present in os.environ wins over .env (python-dotenv never
# overrides), so providers fall back to their mocks. Opt back in with
# AIA_TESTS_USE_REAL_KEYS=1 for a deliberate live smoke run.
if not os.getenv("AIA_TESTS_USE_REAL_KEYS"):
    for _key in (
        "LLM_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY",
        "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "AI_GATEWAY_API_KEY", "CEREBRAS_API_KEY", "STT_API_KEY", "DEEPINFRA_API_KEY",
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "SMTP_HOST", "ADMIN_PASSWORD",
    ):
        os.environ[_key] = ""

# Must be set BEFORE app modules import get_settings().
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DB.as_posix()}")
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "1000")
os.environ.setdefault("COOKIE_SECURE", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.ratelimit import reset_rate_limits  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db():
    """Recreate the schema and clear rate-limit buckets for every test."""
    from app.providers.llm import registry as _llm_registry

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_rate_limits()
    _llm_registry.invalidate_model_overrides()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def register(client: TestClient, email: str, password: str = "Passw0rd123", name: str = "Test"):
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "name": name},
    )


def login(client: TestClient, email: str, password: str = "Passw0rd123"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def csrf_headers(client: TestClient) -> dict:
    """Read the readable CSRF cookie and build the header for unsafe requests."""
    token = client.cookies.get("aia_csrf")
    return {"X-CSRF-Token": token} if token else {}


def authenticate(client: TestClient, email: str = "tester@example.com") -> None:
    """Register + log in a user on the given client so protected routes work."""
    register(client, email)
