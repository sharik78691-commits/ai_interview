"""Password hashing (Argon2id) and policy helpers.

Passwords are NEVER stored in plaintext, NEVER logged, and NEVER returned by
any API. Argon2id is the default algorithm of ``argon2-cffi``.
"""
import logging

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError

logger = logging.getLogger(__name__)

# Argon2id with sensible defaults (argon2-cffi uses Argon2id by default).
_hasher = PasswordHasher()

# A pre-computed dummy hash used to keep login timing constant when the email
# does not exist (account-enumeration resistance).
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing-equalisation")


def hash_password(password: str) -> str:
    """Return an Argon2id hash. Never log the input."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-ish time verification. Returns False for NULL/blank hashes."""
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:  # pragma: no cover - defensive
        logger.warning("Password verification failed unexpectedly")
        return False


def dummy_verify() -> None:
    """Burn the same work as a real verify to avoid timing leaks."""
    try:
        _hasher.verify(_DUMMY_HASH, "dummy-password-for-timing-equalisation")
    except Exception:
        pass


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return False
