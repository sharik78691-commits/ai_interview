"""Shared test fixtures.

Uses an isolated in-memory SQLite database and a deterministic session secret
so tests never touch the developer's real database or .env values.
"""
import os

# Must be set BEFORE app modules import get_settings().
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_auth.db")
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
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_rate_limits()
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
