"""Password hashing (Argon2id) and policy helpers.

Passwords are NEVER stored in plaintext, NEVER logged, and NEVER returned by
any API. Argon2id is the default algorithm of ``argon2-cffi``.
"""
import logging
import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Argon2id with sensible defaults (argon2-cffi uses Argon2id by default).
_hasher = PasswordHasher()

# Each hash/verify allocates ``memory_cost`` KiB (~64 MiB by default). Callers
# run in FastAPI's threadpool, so a login burst could otherwise run dozens at
# once and OOM a small instance. Extra callers simply wait their turn.
_slots = threading.BoundedSemaphore(get_settings().password_hash_concurrency)

# A pre-computed dummy hash used to keep login timing constant when the email
# does not exist (account-enumeration resistance).
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing-equalisation")


def hash_password(password: str) -> str:
    """Return an Argon2id hash. Never log the input."""
    with _slots:
        return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-ish time verification. Returns False for NULL/blank hashes."""
    if not password_hash:
        return False
    try:
        with _slots:
            return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:  # pragma: no cover - defensive
        logger.warning("Password verification failed unexpectedly")
        return False


def dummy_verify() -> None:
    """Burn the same work as a real verify to avoid timing leaks."""
    try:
        with _slots:
            _hasher.verify(_DUMMY_HASH, "dummy-password-for-timing-equalisation")
    except Exception:
        pass


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return False
