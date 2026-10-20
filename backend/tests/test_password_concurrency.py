"""Argon2 work is capped so a login burst cannot OOM a 512 MB instance."""
import threading
import time

from argon2 import PasswordHasher

from app.auth import password as pw


class _CountingHasher:
    """Stands in for PasswordHasher and records peak concurrent calls."""

    def __init__(self):
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    def _work(self):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.05)
        with self._lock:
            self.active -= 1

    def hash(self, _password):
        self._work()
        return "h"

    def verify(self, _hash, _password):
        self._work()
        return True


def test_concurrent_hash_and_verify_are_capped(monkeypatch):
    fake = _CountingHasher()
    monkeypatch.setattr(pw, "_hasher", fake)
    monkeypatch.setattr(pw, "_slots", threading.BoundedSemaphore(2))

    calls = [lambda: pw.hash_password("x"), lambda: pw.verify_password("x", "h"), pw.dummy_verify]
    threads = [threading.Thread(target=calls[i % 3]) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert fake.peak == 2


def test_hashes_made_before_the_cap_still_verify():
    legacy = PasswordHasher().hash("correct horse")
    assert pw.verify_password("correct horse", legacy)
    assert not pw.verify_password("wrong", legacy)
    assert not pw.needs_rehash(legacy)


def test_concurrency_setting_defaults_and_floors(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("PASSWORD_HASH_CONCURRENCY", raising=False)
    assert Settings().password_hash_concurrency == 2
    monkeypatch.setenv("PASSWORD_HASH_CONCURRENCY", "0")
    assert Settings().password_hash_concurrency == 1
    monkeypatch.setenv("PASSWORD_HASH_CONCURRENCY", "abc")
    assert Settings().password_hash_concurrency == 2
