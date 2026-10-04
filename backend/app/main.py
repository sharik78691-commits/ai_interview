"""FastAPI application entrypoint."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, health, interview, resume, websocket
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.core.middleware import SecurityHeadersMiddleware
from app.db.session import init_db

setup_logging()
# get_settings() loads backend/.env, so env vars are ready before it is called.
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (SQLite dev / Postgres prod). No migrations
    # framework is required for the MVP; the schema is created idempotently.
    init_db()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

# Explicit CORS. Credentialed auth forbids allow_origins=["*"].
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)

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
