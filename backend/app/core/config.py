"""Application settings (no pydantic-settings dependency)."""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load backend/.env explicitly. Without an explicit path, python-dotenv only
# searches from the CURRENT WORKING DIRECTORY upwards, so starting the server
# from the project root (instead of backend/) silently missed every variable —
# which looks like "AI/STT not configured".
_BACKEND_DIR = Path(__file__).resolve().parents[2]
load_dotenv(_BACKEND_DIR / ".env")
load_dotenv()


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _as_list(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return default
    return [v.strip() for v in value.split(",") if v.strip()]


class Settings:
    def __init__(self) -> None:
        self.app_name: str = os.getenv("APP_NAME", "AI Interview Assistant")
        self.llm_api_key: str = os.getenv("LLM_API_KEY", "")
        self.llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        self.stt_api_key: str = os.getenv("STT_API_KEY", "")
        self.stt_model: str = os.getenv("STT_MODEL", "whisper-large-v3-turbo")
        # Server-side STT only. Browsers cannot transcribe tab/meeting audio
        # locally, so that audio is streamed to the backend for transcription.
        # Defaults to the LLM endpoint (Groq serves Whisper on the same base URL).
        self.stt_base_url: str = os.getenv("STT_BASE_URL", "")
        # Demo mode defaults to True when no LLM key is configured.
        explicit_demo = os.getenv("DEMO_MODE")
        if explicit_demo is None:
            self.demo_mode: bool = not bool(self.llm_api_key)
        else:
            self.demo_mode = _as_bool(explicit_demo, default=True)
        try:
            self.max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "10"))
        except ValueError:
            self.max_upload_mb = 10
        self.cors_origins: list[str] = _as_list(
            os.getenv("CORS_ORIGINS"), ["http://localhost:4200"]
        )

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def stt_configured(self) -> bool:
        """Interviewer audio transcription available (falls back to LLM key)."""
        return bool(self.stt_api_key or self.llm_api_key)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
