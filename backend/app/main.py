"""FastAPI application entrypoint."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import auth, health, interview, resume, websocket
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.middleware import RequestLoggingMiddleware, SecurityHeadersMiddleware
from app.db.session import init_db

setup_logging()
logger = logging.getLogger(__name__)

# get_settings() loads backend/.env, so env vars are ready before it is called.
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (SQLite dev / Postgres prod). No migrations
    # framework is required for the MVP; the schema is created idempotently.
    logger.info("=" * 72)
    logger.info("Starting %s", settings.app_name)
    logger.info("Environment      : %s", "production" if settings.is_production else "development")
    logger.info("Demo mode        : %s", settings.demo_mode)
    logger.info("LLM configured   : %s (model=%s)", settings.llm_configured, settings.llm_model)
    logger.info("STT configured   : %s (model=%s)", settings.stt_configured, settings.stt_model)
    logger.info("Google OAuth     : %s", settings.google_configured)
    logger.info("CORS origins     : %s", settings.cors_origins)
    logger.info("Frontend URL     : %s", settings.frontend_url)
    logger.info("Backend URL      : %s", settings.backend_url)
    logger.info("Google redirect  : %s", settings.google_redirect_uri)
    logger.info(
        "Cookies          : secure=%s samesite=%s domain=%r",
        settings.cookie_secure,
        settings.cookie_samesite,
        settings.cookie_domain or "",
    )
    logger.info(
        "Session secret   : %s",
        "configured" if settings.session_secret else "MISSING (using insecure dev default)",
    )
    logger.info("=" * 72)
    init_db()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

# Explicit CORS. Credentialed auth forbids allow_origins=["*"].
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    # Browsers must be told the response may be exposed cross-origin for the
    # credentialed (cookie-bearing) requests the SPA makes from Vercel to
    # Render. Without this, fetch/logout from the SPA fails preflight.
    allow_credentials=True,
)
app.add_middleware(SecurityHeadersMiddleware)
# Added LAST so it is the OUTERMOST layer: requests that CORS rejects never
# reach a route, and without this they would produce no server-side log at all.
app.add_middleware(RequestLoggingMiddleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the full traceback for any unhandled error.

    FastAPI returns a bare 500 for unexpected exceptions; without this handler
    the traceback only appears in the server log, so the client sees an opaque
    failure with no correlation to the cause.
    """
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    origin = request.headers.get("origin")
    headers = {}
    # Re-add CORS headers on error responses, otherwise the browser reports the
    # failure as a CORS problem instead of the real 500.
    if origin and origin in settings.cors_origins:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
        headers["Vary"] = "Origin"
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
        headers=headers,
    )


app.include_router(health.router, prefix="/api")
# Root-level alias: some probes/clients hit /health directly.
app.include_router(health.router)
app.include_router(auth.router, prefix="/api")
app.include_router(resume.router, prefix="/api")
app.include_router(interview.router, prefix="/api")
app.include_router(websocket.router)


@app.get("/")
async def root() -> dict:
    return {
        "name": settings.app_name,
        "version": "0.1.0",
        "docs": "/docs",
    }
