"""Application settings (no pydantic-settings dependency)."""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load backend/.env explicitly. Without an explicit path, python-dotenv only
# searches from the CURRENT WORKING DIRECTORY upwards, so starting the server
# from the project root (instead of backend/) silently missed every variable —
# which looks like "AI/STT not configured".
_BACKEND_DIR = Path(__file__).resolve().parents[2]
env_file = _BACKEND_DIR / ".env"
if env_file.exists():
    logger.info("Loading environment from %s", env_file)
    load_dotenv(env_file)
else:
    # Not fatal: on Render (and any container host) configuration comes from
    # real environment variables, and no .env file is shipped.
    logger.info(
        "No .env file at %s; relying on process environment variables", env_file
    )
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
        logger.info("Initializing application settings...")
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

        # ------------------------------------------------------------- database
        # SQLite by default so the app starts locally with zero infrastructure.
        # Production sets DATABASE_URL to a PostgreSQL DSN.
        self.database_url: str = os.getenv(
            "DATABASE_URL", f"sqlite:///{(_BACKEND_DIR / 'app.db').as_posix()}"
        )

        # -------------------------------------------------------------- session
        # Secret used to sign session + CSRF cookies. MUST be set in production.
        self.session_secret: str = os.getenv("SESSION_SECRET", "")
        # Session lifetime (seconds). Short-lived; server-side validated.
        try:
            self.session_max_age: int = int(os.getenv("SESSION_MAX_AGE", "604800"))
        except ValueError:
            self.session_max_age = 604800
        # Password-reset token lifetime (seconds).
        try:
            self.reset_token_max_age: int = int(
                os.getenv("RESET_TOKEN_MAX_AGE", "3600")
            )
        except ValueError:
            self.reset_token_max_age = 3600

        # ---------------------------------------------------------------- URLs
        # Resolved BEFORE Google so the redirect URI can default to the backend
        # origin. This matters in production: the OAuth callback is served by
        # the backend, so the redirect URI must be the backend's PUBLIC URL —
        # not localhost. Google rejects any redirect_uri that is not registered
        # verbatim in the OAuth client ("Error 400: redirect_uri_mismatch").
        self.frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:4200")
        self.backend_url: str = os.getenv("BACKEND_URL", "http://localhost:8000")

        # Guard against the classic production misconfiguration where
        # FRONTEND_URL is left unset: the OAuth callback then redirects users
        # to http://localhost:4200 (which only exists on the developer's
        # machine). Surface it loudly at startup instead of silently failing.
        if self._is_production() and "localhost" in self.frontend_url:
            import logging

            logging.getLogger(__name__).warning(
                "FRONTEND_URL is still '%s' while ENVIRONMENT=production. "
                "OAuth callbacks and password-reset links will point at "
                "localhost. Set FRONTEND_URL to your public frontend origin.",
                self.frontend_url,
            )

        # --------------------------------------------------------------- Google
        self.google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
        self.google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
        # Default the redirect URI to the backend origin so a deployed backend
        # never falls back to localhost. Override with GOOGLE_REDIRECT_URI when
        # the callback is fronted by a different host (e.g. a reverse proxy).
        self.google_redirect_uri: str = os.getenv(
            "GOOGLE_REDIRECT_URI",
            f"{self.backend_url.rstrip('/')}/api/auth/google/callback",
        )

        # --------------------------------------------------------------- cookies
        # Secure cookies are forced on in production (HTTPS). Local dev over
        # http://localhost keeps them off so the browser still stores them.
        self.cookie_secure: bool = _as_bool(
            os.getenv("COOKIE_SECURE"), default=self._is_production()
        )
        # SameSite: "lax" works for the OAuth redirect back to the app while
        # still blocking cross-site POSTs (CSRF). "none" requires Secure.
        self.cookie_samesite: str = os.getenv("COOKIE_SAMESITE", "lax").lower()
        # NOTE: leave empty for the normal topology (SPA on oyeinterview.com,
        # API proxied on the same origin). A browser REJECTS a cookie whose
        # Domain attribute does not cover the host that served the response, so
        # setting this to the Render host from an oyeinterview.com response
        # would silently drop the session cookie.
        self.cookie_domain: str = os.getenv("COOKIE_DOMAIN", "")

        # ------------------------------------------------------------ websocket
        # Lifetime of the short-lived ticket used to authenticate the live
        # interview socket. The socket is opened directly against the backend
        # host (static hosts cannot proxy a WS upgrade), which is a different
        # origin than the site that holds the session cookie — so the cookie is
        # never sent and cookie auth cannot work there. A ticket in the URL
        # replaces it. Kept very short because URLs are logged by proxies.
        try:
            self.ws_ticket_max_age: int = int(os.getenv("WS_TICKET_MAX_AGE", "60"))
        except ValueError:
            self.ws_ticket_max_age = 60

        # --------------------------------------------------------- rate limiting
        try:
            self.rate_limit_per_minute: int = int(
                os.getenv("RATE_LIMIT_PER_MINUTE", "10")
            )
        except ValueError:
            self.rate_limit_per_minute = 10

        self._log_summary()

    def _is_production(self) -> bool:
        env = os.getenv("ENVIRONMENT", os.getenv("APP_ENV", "development")).lower()
        return env in ("production", "prod")

    def _log_summary(self) -> None:
        """Emit the resolved configuration once, at startup.

        Misconfiguration is the most common cause of production-only failures
        (the app works on localhost because the defaults are all localhost
        values). Logging the resolved values — not the raw env — makes the
        difference between "should work" and "does work" immediately visible.
        """
        logger.info("Resolved configuration:")
        logger.info("  environment      = %s", self._is_production() and "production" or "development")
        logger.info("  cors_origins     = %s", self.cors_origins)
        logger.info("  frontend_url     = %s", self.frontend_url)
        logger.info("  backend_url      = %s", self.backend_url)
        logger.info("  database_url     = %s", self._redact_dsn(self.database_url))
        logger.info(
            "  cookies          = secure=%s samesite=%s domain=%r",
            self.cookie_secure,
            self.cookie_samesite,
            self.cookie_domain or "",
        )
        logger.info("  llm_configured   = %s", self.llm_configured)
        logger.info("  stt_configured   = %s", self.stt_configured)
        logger.info("  demo_mode        = %s", self.demo_mode)
        logger.info("  google_oauth     = %s", self.google_configured)
        logger.info("  ws_ticket_max_age= %ss", self.ws_ticket_max_age)

        if not self.session_secret:
            logger.warning(
                "SESSION_SECRET is not set. Sessions are signed with an insecure "
                "built-in default, so every restart invalidates all logins. Set "
                "SESSION_SECRET in production."
            )
        if self.google_configured:
            # The OAuth callback MUST be served from the SAME origin as the login
            # start, otherwise the one-time state cookie (scoped to the login
            # origin) is never sent back and every sign-in fails with
            # "google_state" / "OAuth state mismatch (cookie_present=False)".
            redirect_host = self._host_of(self.google_redirect_uri)
            frontend_host = self._host_of(self.frontend_url)
            if redirect_host and frontend_host and redirect_host != frontend_host:
                logger.warning(
                    "GOOGLE_REDIRECT_URI host (%s) differs from FRONTEND_URL host "
                    "(%s). The callback must be served from the SAME origin as the "
                    "login start (the frontend proxies /api to the backend), "
                    "otherwise the aia_oauth_state cookie is dropped and sign-in "
                    "fails with 'google_state'. Point GOOGLE_REDIRECT_URI at "
                    "%s/api/auth/google/callback.",
                    redirect_host,
                    frontend_host,
                    self.frontend_url.rstrip("/"),
                )
        if not self.cors_origins:
            logger.error(
                "CORS_ORIGINS is empty. No browser origin will be allowed to "
                "call the API; the SPA will fail with opaque CORS errors."
            )
        if self.cookie_domain:
            # Fires regardless of ENVIRONMENT: a cross-host cookie Domain is
            # harmful in the proxy topology (SPA on one host, /api proxied to the
            # backend) whether or not "production" is set. A browser REJECTS a
            # cookie whose Domain does not cover the host that served it, which
            # silently drops the session + OAuth-state cookies and surfaces as
            # "logged out on production" / "OAuth state mismatch".
            logger.warning(
                "COOKIE_DOMAIN=%r is set. A browser rejects a cookie whose Domain "
                "does not cover the host that served the response, which silently "
                "drops the session cookie. Leave COOKIE_DOMAIN empty when the SPA "
                "is served on one host and /api is proxied to the backend (the "
                "normal Vercel->Render topology). Only set it when the SPA and API "
                "genuinely share a parent domain.",
                self.cookie_domain,
            )
        if self._is_production() and "localhost" in self.frontend_url:
            # Already warned in __init__; kept here so the summary is complete.
            logger.warning("FRONTEND_URL still points at localhost in production.")

    @staticmethod
    def _redact_dsn(dsn: str) -> str:
        """Hide the password in a DSN before logging it."""
        if "://" not in dsn:
            return dsn
        scheme, rest = dsn.split("://", 1)
        if "@" not in rest:
            return dsn
        creds, host = rest.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"

    @staticmethod
    def _host_of(url: str) -> str:
        """Lowercased hostname of a URL (empty when unparseable).

        Used to cross-check that the OAuth redirect URI and the frontend URL
        share an origin — the single most common production OAuth failure.
        """
        try:
            from urllib.parse import urlsplit

            return (urlsplit(url).hostname or "").lower()
        except ValueError:
            return ""

    def cors_allows(self, origin: str | None) -> bool:
        """True when the given browser origin is allowed by CORS."""
        if not origin:
            return False
        return origin in self.cors_origins

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def stt_configured(self) -> bool:
        """Interviewer audio transcription available (falls back to LLM key)."""
        return bool(self.stt_api_key or self.llm_api_key)

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def is_production(self) -> bool:
        return self._is_production()


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
