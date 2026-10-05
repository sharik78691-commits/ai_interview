"""Central logging setup.

Logging is intentionally verbose in this project: almost every production
failure so far (WebSocket 403, cross-site cookie loss, OAuth state mismatch,
STT 400s) presented as an opaque status code on the client. The fix in each
case was to log the *decision inputs* (origin, cookie names, host, resolved
config) on the server, so those are logged by default here.

Environment variables
---------------------
LOG_LEVEL
    Root level. Defaults to ``INFO``; set ``DEBUG`` for handshake-level detail.
LOG_FORMAT
    ``text`` (default) for a readable single-line format, ``json`` for
    newline-delimited JSON (nicer for Render/Datadog log search).
"""
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# backend/ directory — logs are written here, independent of the CWD the server
# was started from (same reasoning as .env loading in config.py).
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_LOG_DIR = _BACKEND_DIR / "logs"

# Rotating file size + how many backups to keep before the oldest is deleted.
_MAX_LOG_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3


class JsonFormatter(logging.Formatter):
    """Minimal NDJSON formatter for hosted log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


# Third-party loggers that are noisy at INFO/DEBUG and drown out our own
# diagnostics in the hosted log stream.
_NOISY_LOGGERS = {
    "uvicorn.access": logging.WARNING,
    "uvicorn.error": logging.INFO,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "urllib3": logging.WARNING,
    "multipart": logging.WARNING,
    "python_multipart": logging.WARNING,
    "sqlalchemy.engine": logging.WARNING,
    "watchfiles": logging.WARNING,
}


def setup_logging(level: int | None = None) -> logging.Logger:
    """Configure root logging once, idempotently.

    Safe to call from multiple entrypoints (``main.py``, tests, CLI scripts):
    handlers are only attached if the root logger has none.
    """
    if level is None:
        raw = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        level = getattr(logging, raw, logging.INFO)

    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        if os.getenv("LOG_FORMAT", "text").strip().lower() == "json":
            handler.setFormatter(JsonFormatter())
        else:
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
        root.addHandler(handler)

        # File sinks: errors always persist to disk (survives restarts and is
        # searchable even after the console scrolls past them). The full
        # per-request log is optional via LOG_TO_FILE.
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            error_handler = RotatingFileHandler(
                _LOG_DIR / "error.log",
                maxBytes=_MAX_LOG_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            root.addHandler(error_handler)

            if os.getenv("LOG_TO_FILE", "").strip().lower() in ("1", "true", "yes", "on"):
                app_handler = RotatingFileHandler(
                    _LOG_DIR / "app.log",
                    maxBytes=_MAX_LOG_BYTES,
                    backupCount=_BACKUP_COUNT,
                    encoding="utf-8",
                )
                app_handler.setLevel(level)
                app_handler.setFormatter(
                    logging.Formatter(
                        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S",
                    )
                )
                root.addHandler(app_handler)
        except OSError as exc:
            # Logging must never crash the app, even if the log dir is unwritable
            # (read-only filesystem, permissions, containerised /app mount...).
            logging.getLogger(__name__).warning(
                "Could not configure file logging in %s: %s", _LOG_DIR, exc
            )

    root.setLevel(level)
    for name, noisy_level in _NOISY_LOGGERS.items():
        # Never make a noisy logger MORE verbose than the requested level.
        logging.getLogger(name).setLevel(max(noisy_level, level))

    logging.getLogger(__name__).info("Logging configured (level=%s)", logging.getLevelName(level))
    return logging.getLogger("ai_interview")


def get_logger(name: str) -> logging.Logger:
    """Convenience accessor used by modules that prefer an explicit logger."""
    return logging.getLogger(name)
