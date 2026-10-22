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
    # Blank counts as unset: `.env.example` ships `COOKIE_SECURE=` etc., and an
    # empty value must mean "use the default" (e.g. Secure cookies in
    # production), not "false".
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _currency_set(value: str | None) -> frozenset[str]:
    """'INR, usd' -> {'INR', 'USD'} (3-letter codes only)."""
    return frozenset(
        c.strip().upper() for c in (value or "").split(",")
        if len(c.strip()) == 3 and c.strip().isalpha()
    )


def _env(name: str, default: str) -> str:
    """``os.getenv`` that also falls back to ``default`` for a BLANK value."""
    value = os.getenv(name)
    return value if value and value.strip() else default


def _as_list(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return default
    return [v.strip() for v in value.split(",") if v.strip()]


class Settings:
    def __init__(self) -> None:
        logger.info("Initializing application settings...")
        self.app_name: str = os.getenv("APP_NAME", "AI Interview Assistant")
        self.llm_api_key: str = os.getenv("LLM_API_KEY", "")
        self.llm_model: str = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
        self.llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
        # ------------------------------------------------------------ multi-LLM
        # Additional provider keys/models. Keys stay ONLY in the backend env —
        # never in the browser or in API responses. A model is "available" only
        # when its provider key is present.
        # Groq key falls back to the legacy LLM_API_KEY for backward compat.
        self.groq_api_key: str = os.getenv("GROQ_API_KEY", "") or self.llm_api_key
        self.gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
        # Gemini Flash-Lite on Google's official OpenAI-compatible endpoint.
        self.gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
        # Gemini 3.5 Flash (full Flash) — a second Gemini option (replaces the retired 2.5 Flash).
        self.gemini_35_model: str = os.getenv("GEMINI_35_MODEL", "gemini-3.5-flash")
        # Gemini 3.1 Flash-Lite — a third selectable Gemini option.
        self.gemini_31_lite_model: str = os.getenv(
            "GEMINI_31_LITE_MODEL", "gemini-3.1-flash-lite"
        )
        # Desktop screenshot analysis (POST /api/screenshots/analyze). Uses
        # GEMINI_API_KEY above — the desktop app never holds a key.
        self.screenshot_model: str = os.getenv("SCREENSHOT_MODEL", "") or self.gemini_model
        # Qwen + the OpenRouter free model share the OpenRouter key.
        self.openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
        self.qwen_model: str = os.getenv("QWEN_MODEL", "qwen/qwen3-coder:free")
        self.openrouter_model: str = os.getenv(
            "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"
        )
        # OpenAI native API (GPT-6 Luna etc.).
        self.openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
        self.openai_model: str = os.getenv("OPENAI_MODEL", "gpt-6-luna")
        # DeepSeek (OpenAI-compatible endpoint, https://api.deepseek.com/v1).
        # Two selectable models: V4.1 Flash (speed tier) + V4 Pro (capability).
        self.deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
        self.deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
        self.deepseek_v4_pro_model: str = os.getenv(
            "DEEPSEEK_V4_PRO_MODEL", "deepseek-v4-pro"
        )
        # Vercel AI Gateway (OpenAI-compatible, https://ai-gateway.vercel.sh/v1):
        # one key, billed through Vercel. Serves DeepSeek V4.1 Flash without a
        # DeepSeek account balance. Model ids are "<provider>/<model>".
        self.ai_gateway_api_key: str = os.getenv("AI_GATEWAY_API_KEY", "")
        self.ai_gateway_base_url: str = (
            os.getenv("AI_GATEWAY_BASE_URL", "") or "https://ai-gateway.vercel.sh/v1"
        )
        self.gateway_deepseek_flash_model: str = (
            os.getenv("GATEWAY_DEEPSEEK_FLASH_MODEL", "") or "deepseek/deepseek-v4.1-flash-1017"
        )
        self.gateway_gpt_oss_model: str = (
            os.getenv("GATEWAY_GPT_OSS_MODEL", "") or "openai/gpt-oss-120b"
        )
        # Cerebras Inference (OpenAI-compatible, https://api.cerebras.ai/v1).
        # Fastest measured host for GPT-OSS 120B (~1s full answer). Developer
        # tier limits: 5 requests/min, 150/hour, 2400/day, 30k tokens/min.
        self.cerebras_api_key: str = os.getenv("CEREBRAS_API_KEY", "")
        self.cerebras_base_url: str = (
            os.getenv("CEREBRAS_BASE_URL", "") or "https://api.cerebras.ai/v1"
        )
        self.cerebras_gpt_oss_model: str = (
            os.getenv("CEREBRAS_GPT_OSS_MODEL", "") or "gpt-oss-120b"
        )
        self.stt_api_key: str = os.getenv("STT_API_KEY", "")
        self.stt_model: str = os.getenv("STT_MODEL", "whisper-large-v3-turbo")
        # Forcing the language skips Whisper's auto-detect (faster, and stops
        # accented English being misread as another language). Empty = auto.
        self.stt_language: str = os.getenv("STT_LANGUAGE", "en").strip()
        # Second STT model (OpenAI's gpt-4o-mini-transcribe).
        self.stt_model_2: str = os.getenv("STT_MODEL_2", "gpt-4o-mini-transcribe")
        # Third STT model (Google's Gemini 3.5 Transcribe).
        self.stt_model_3: str = os.getenv("STT_MODEL_3", "gemini-3.5-transcribe")
        # Fourth STT model (Google's Gemini 3.5 Transcribe Live — streaming
        # Live API / bidiGenerateContent over a WebSocket; uses GEMINI_API_KEY).
        self.stt_model_4: str = os.getenv("STT_MODEL_4", "") or "gemini-3.5-transcribe-live"
        # Fifth STT model: the same Whisper large-v3-turbo served by DeepInfra
        # (OpenAI-compatible /audio/transcriptions). Own key: DEEPINFRA_API_KEY.
        self.deepinfra_api_key: str = os.getenv("DEEPINFRA_API_KEY", "")
        self.stt_model_5: str = os.getenv("STT_MODEL_5", "") or "openai/whisper-large-v3-turbo"
        # Server-side STT only. Browsers cannot transcribe tab/meeting audio
        # locally, so that audio is streamed to the backend for transcription.
        # Defaults to the LLM endpoint (Groq serves Whisper on the same base URL).
        self.stt_base_url: str = os.getenv("STT_BASE_URL", "")
        # Live transcript: while the interviewer is still talking, the client
        # sends rolling snapshots of the clip and the backend returns PROVISIONAL
        # text (display only, never question detection). Costs ~1 extra STT call
        # per second of speech; set false to show text only after each pause.
        self.stt_live_preview: bool = _as_bool(os.getenv("STT_LIVE_PREVIEW"), default=True)
        # One-time free trial for every account (see free_trial_service): live
        # interview minutes on the website, separate minutes in the desktop
        # app, and screenshot analyses. After it, live interview needs a plan.
        # FREE_TRIAL_ENFORCED=false turns the live-interview gate off.
        self.free_trial_enforced: bool = _as_bool(os.getenv("FREE_TRIAL_ENFORCED"), default=True)
        self.free_web_minutes: int = max(0, int(os.getenv("FREE_WEB_MINUTES", "20")))
        self.free_desktop_minutes: int = max(0, int(os.getenv("FREE_DESKTOP_MINUTES", "15")))
        self.free_screenshots: int = max(0, int(os.getenv("FREE_SCREENSHOTS", "2")))
        # Word-by-word live transcript: with GEMINI_API_KEY set, interviewer
        # audio is also STREAMED into one Gemini Live session (display only;
        # answers still come from the clip pipeline). Without the key, the
        # snapshot previews above are used instead.
        self.stt_live_model: str = os.getenv("STT_LIVE_MODEL", "") or self.stt_model_4
        # Answer from that live stream: once its text holds a finished question
        # and the interviewer pauses, the answer starts without waiting for the
        # clip's silence cut + batch STT (~1-2s sooner). The clip pipeline stays
        # as the fallback for speech the stream missed. false = clips only.
        self.stt_live_answers: bool = _as_bool(os.getenv("STT_LIVE_ANSWERS"), default=True)
        # Demo mode defaults to True when no LLM key is configured.
        explicit_demo = os.getenv("DEMO_MODE")
        if explicit_demo is None or not explicit_demo.strip():
            self.demo_mode: bool = not bool(self.llm_api_key)
        else:
            self.demo_mode = _as_bool(explicit_demo, default=True)
        try:
            self.max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "10"))
        except ValueError:
            self.max_upload_mb = 10
        # Each Argon2 hash/verify holds ~64 MiB for ~0.5 s; capping how many
        # run at once keeps a login burst from OOM-killing a 512 MB instance
        # (which would drop every live interview socket).
        try:
            self.password_hash_concurrency: int = max(
                1, int(os.getenv("PASSWORD_HASH_CONCURRENCY", "2"))
            )
        except ValueError:
            self.password_hash_concurrency = 2
        self.cors_origins: list[str] = _as_list(
            os.getenv("CORS_ORIGINS"), ["http://localhost:4200"]
        )

        # ------------------------------------------------------------- database
        # SQLite by default so the app starts locally with zero infrastructure.
        # Production sets DATABASE_URL to a PostgreSQL DSN.
        self.database_url: str = _env(
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
        self.google_redirect_uri: str = _env(
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

        # ---------------------------------------------------------------- admin
        # Seed admin login. The password is NEVER hardcoded: it comes only from
        # the environment, and seeding is skipped (with a warning) when unset.
        # Local dev: put ADMIN_PASSWORD in backend/.env (gitignored).
        # Production: set ADMIN_PASSWORD on the host (Render env vars).
        self.admin_email: str = os.getenv("ADMIN_EMAIL", "admin@oyeinterview.com")
        self.admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
        self.admin_name: str = os.getenv("ADMIN_NAME", "Administrator")
        self.admin_password: str = os.getenv("ADMIN_PASSWORD", "")

        # ---------------------------------------------------------------- email
        # Outgoing mail for password-reset links. Any SMTP host works; for
        # Gmail use an App Password (Google Account > Security > 2-Step
        # Verification > App passwords) — a normal login password is rejected.
        # Empty SMTP_HOST = mail disabled: reset links are only written to the
        # server log (local-dev fallback), the API response stays the same.
        self.smtp_host: str = os.getenv("SMTP_HOST", "")
        try:
            self.smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
        except ValueError:
            self.smtp_port = 587
        self.smtp_user: str = os.getenv("SMTP_USER", "")
        self.smtp_password: str = os.getenv("SMTP_PASSWORD", "")
        self.smtp_from: str = os.getenv("SMTP_FROM", "") or self.smtp_user
        self.smtp_use_tls: bool = _as_bool(os.getenv("SMTP_USE_TLS"), default=True)

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
        # Per-user cap on AI answer requests (REST /interview/analyze) and
        # resume uploads. Protects provider spend from a scripted client.
        try:
            self.ai_rate_limit_per_minute: int = int(
                os.getenv("AI_RATE_LIMIT_PER_MINUTE", "30")
            )
        except ValueError:
            self.ai_rate_limit_per_minute = 30
        # Per-ADMIN-USER cap on /api/admin/* (dashboard reads + admin actions).
        # The admin page fires several requests per load and polls costs, so
        # it must not share the tiny per-IP login limit above.
        try:
            self.admin_rate_limit_per_minute: int = int(
                os.getenv("ADMIN_RATE_LIMIT_PER_MINUTE", "120")
            )
        except ValueError:
            self.admin_rate_limit_per_minute = 120
        # Number of reverse proxies in front of the app that APPEND to
        # X-Forwarded-For. 0 (default) keeps the legacy behaviour (left-most
        # entry). Set it to the real hop count (e.g. 2 for Vercel -> Render) so
        # a client cannot pick its own rate-limit bucket by spoofing the header.
        try:
            self.trusted_proxy_count: int = max(0, int(os.getenv("TRUSTED_PROXY_COUNT", "0")))
        except ValueError:
            self.trusted_proxy_count = 0
        # Interactive API docs (/docs, /redoc, /openapi.json). On by default in
        # development, off in production unless explicitly enabled.
        self.enable_api_docs: bool = _as_bool(
            os.getenv("ENABLE_API_DOCS"), default=not self._is_production()
        )

        # ------------------------------------------------------------ payments
        # Credentials come ONLY from the environment; a gateway is offered to
        # users only when its credentials are present. Never logged.
        self.razorpay_key_id: str = os.getenv("RAZORPAY_KEY_ID", "").strip()
        self.razorpay_key_secret: str = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
        self.razorpay_webhook_secret: str = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()
        self.cashfree_client_id: str = os.getenv("CASHFREE_CLIENT_ID", "").strip()
        self.cashfree_client_secret: str = os.getenv("CASHFREE_CLIENT_SECRET", "").strip()
        # "sandbox" (default) or "production" — selects the Cashfree API host.
        self.cashfree_env: str = _env("CASHFREE_ENV", "sandbox").strip().lower()
        self.cashfree_api_version: str = _env("CASHFREE_API_VERSION", "2023-08-01").strip()
        # Development-only simulated gateway. Forced OFF in production no
        # matter what the variable says, so a fake payment can never grant
        # real access.
        self.payments_mock_enabled: bool = (
            _as_bool(os.getenv("PAYMENTS_MOCK_ENABLED"), default=not self._is_production())
            and not self._is_production()
        )
        # Currencies each gateway may charge, comma-separated ISO codes.
        # Default INR only: international currencies need the merchant
        # account's international payments enabled at the gateway first
        # (Razorpay: International Payments; Cashfree: global collections).
        self.razorpay_currencies: frozenset[str] = _currency_set(os.getenv("RAZORPAY_CURRENCIES", "INR"))
        self.cashfree_currencies: frozenset[str] = _currency_set(os.getenv("CASHFREE_CURRENCIES", "INR"))
        # Name shown in gateway checkout popups.
        self.payment_merchant_name: str = _env("PAYMENT_MERCHANT_NAME", "OyeInterview")

        self._log_summary()

    @property
    def razorpay_configured(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def cashfree_configured(self) -> bool:
        return bool(self.cashfree_client_id and self.cashfree_client_secret)

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
        logger.info("  gemini_configured= %s", self.gemini_configured)
        logger.info("  openrouter_conf  = %s", self.openrouter_configured)
        logger.info("  deepseek_conf    = %s", self.deepseek_configured)
        logger.info("  ai_gateway_conf  = %s", self.ai_gateway_configured)
        logger.info("  cerebras_conf    = %s", self.cerebras_configured)
        logger.info("  stt_configured   = %s", self.stt_configured)
        logger.info("  demo_mode        = %s", self.demo_mode)
        logger.info("  google_oauth     = %s", self.google_configured)
        logger.info("  ws_ticket_max_age= %ss", self.ws_ticket_max_age)
        logger.info(
            "  payments         = razorpay=%s (webhook_secret=%s) cashfree=%s (%s) mock=%s",
            self.razorpay_configured,
            bool(self.razorpay_webhook_secret),
            self.cashfree_configured,
            self.cashfree_env,
            self.payments_mock_enabled,
        )

        if not self.session_secret and self._is_production():
            logger.error(
                "SESSION_SECRET is not set while ENVIRONMENT=production. A random "
                "per-process secret is used instead (sessions cannot be forged, "
                "but every restart signs everyone out and multiple workers will "
                "not share logins). Set SESSION_SECRET on the host."
            )
        elif not self.session_secret:
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
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def openrouter_configured(self) -> bool:
        return bool(self.openrouter_api_key)

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def deepseek_configured(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def ai_gateway_configured(self) -> bool:
        return bool(self.ai_gateway_api_key)

    @property
    def cerebras_configured(self) -> bool:
        return bool(self.cerebras_api_key)

    @property
    def stt_configured(self) -> bool:
        """Interviewer audio transcription available (falls back to LLM key)."""
        return bool(self.stt_api_key or self.llm_api_key)

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def smtp_configured(self) -> bool:
        """Outbound mail available (host + sender identity present)."""
        return bool(self.smtp_host and self.smtp_from)

    @property
    def is_production(self) -> bool:
        return self._is_production()


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
