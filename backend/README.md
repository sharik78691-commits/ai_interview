# Backend — FastAPI service

The API and live-interview WebSocket behind oyeinterview.com. It covers
authentication (email/password and Google), the resume and target-role libraries,
AI answer generation (multi-provider LLM), speech-to-text for meeting audio, and the
admin panel API.

**Stack:** Python 3.10+ (developed on 3.14), FastAPI, uvicorn, Pydantic v2,
SQLAlchemy 2.x (SQLite locally, PostgreSQL in production), Argon2id, itsdangerous,
Authlib, httpx.

System design: [../ARCHITECTURE.md](../ARCHITECTURE.md) · Security status:
[../docs/AUDIT_REPORT.md](../docs/AUDIT_REPORT.md)

---

## Quick start

```powershell
cd backend
python -m venv .venv; .\.venv\Scripts\Activate.ps1      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                                   # then edit; never commit .env
uvicorn app.main:app --reload --port 8000
```

* Health: `GET http://localhost:8000/api/health`
* Interactive docs (development only): `http://localhost:8000/docs`
* With no LLM key, the app runs in **demo mode** (mock answers, no external calls).

The database and all tables are created automatically on first start. Locally
that is `backend/app.db` (gitignored).

## Configuration

Every setting comes from environment variables. `backend/.env` is loaded
automatically, and real environment variables take precedence.
[`.env.example`](.env.example) documents every variable with safe placeholders.
The ones that matter most:

| Variable | Required | Purpose |
|----------|----------|---------|
| `ENVIRONMENT` | prod | `production` turns on Secure cookies, HSTS, hidden API docs, and no reset links in logs. |
| `SESSION_SECRET` | **prod** | Signs session, CSRF and WS-ticket tokens. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. If it's unset in production, a random per-process secret is used and every restart signs everyone out. |
| `DATABASE_URL` | prod | `postgresql+psycopg://user:pass@host:5432/db`. Empty means local SQLite. |
| `CORS_ORIGINS` | yes | Comma-separated browser origins, e.g. `https://www.oyeinterview.com`. Never `*`. Also gates cookie-auth WebSockets. |
| `FRONTEND_URL` / `BACKEND_URL` | prod | Public origins, used for OAuth redirects and reset links. |
| `LLM_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY` | optional | Provider keys. A model is selectable only when its key is set. Keys never leave the backend. |
| `STT_API_KEY`, `STT_MODEL`, `STT_BASE_URL` | optional | Meeting-audio transcription. Falls back to the LLM key and endpoint. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` | optional | Google sign-in. The redirect URI must be on the **frontend** origin (see troubleshooting). |
| `SMTP_*` | optional | Password-reset email. Without it, reset links appear only in the dev log. |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` / `RAZORPAY_WEBHOOK_SECRET` | optional | Razorpay gateway (offered only when set) |
| `CASHFREE_CLIENT_ID` / `CASHFREE_CLIENT_SECRET` / `CASHFREE_ENV` | optional | Cashfree gateway (`sandbox` by default) |
| `PAYMENTS_MOCK_ENABLED` | — | Dev-only simulated gateway; always off in production |
| `ADMIN_EMAIL` / `ADMIN_USERNAME` / `ADMIN_PASSWORD` | optional | Seeds the admin login at startup (skipped when the password is empty). |
| `RATE_LIMIT_PER_MINUTE` | — | Auth limit per IP **and** per account (default 10). |
| `AI_RATE_LIMIT_PER_MINUTE` | — | Per-user AI answers and resume uploads (default 30). |
| `TRUSTED_PROXY_COUNT` | recommended | Number of proxies that append to `X-Forwarded-For` (Vercel → Render: usually `2`). `0` keeps the legacy, spoofable behaviour. |
| `ENABLE_API_DOCS` | — | Force `/docs` on or off. Default: on in development, off in production. |
| `COOKIE_SECURE`, `COOKIE_SAMESITE`, `COOKIE_DOMAIN` | — | Cookie overrides. Leave `COOKIE_DOMAIN` empty for the Vercel proxy setup. |

The resolved configuration is logged once at startup, with secrets redacted.

## Project layout

```
app/
  main.py           FastAPI app, middleware order, routers, docs toggle
  api/              routers: auth, resume, roles, interview, websocket, admin, ai_models, health
  auth/             sessions + CSRF + WS tickets, dependencies, Google OIDC, Argon2id
  core/             config, middleware (headers/logging), rate limiting, shared HTTP client
  db/               engine/session, init_db() (create + idempotent migrations)
  models/           ORM tables + Pydantic schemas (AIInterviewResponse contract)
  providers/        llm/ (registry, OpenAI-compatible streaming, mock), stt/
  repositories/     owner-scoped data access (resume + target-role libraries)
  services/         AI orchestration, interview-context store, question detection,
                    resume parsing, model-config, activity log, analytics, cost, email
scripts/seed_demo_data.py   optional demo data for the admin dashboards
tests/                      pytest suite
```

## API reference

Full schemas are at `/docs` (development). Every route except health, auth
bootstrap and the Google redirect requires a session. **Every mutating route also
requires the `X-CSRF-Token` header**, which must match the `aia_csrf` cookie.

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| GET | `/api/health`, `/health` | public | Status, demo mode, configured providers |
| POST | `/api/auth/register` | public | Rate-limited; signs the user in |
| POST | `/api/auth/login` | public | Email **or** username. Limited per IP and per account |
| POST | `/api/auth/logout` | session + CSRF | Adds the session to the deny-list |
| GET | `/api/auth/me` | optional | `{authenticated, user}` |
| GET | `/api/auth/csrf` | session | Re-issues the CSRF cookie |
| POST | `/api/auth/ws-ticket` | session | 60 s ticket for `/ws/interview` |
| GET | `/api/auth/google/login` → `/callback` | public | OIDC code flow |
| POST | `/api/auth/forgot-password` | public | Same response whether or not the account exists |
| POST | `/api/auth/reset-password` | token | Single-use token; **signs out all other sessions** |
| GET | `/api/ai/models` | public | Selectable model catalogue (no secrets) |
| POST | `/api/interview/prepare` | session + CSRF | Stores resume/JD context (per user, in memory) |
| GET | `/api/interview/context` | session | Context summary |
| POST | `/api/interview/analyze` | session + CSRF | REST fallback answer. Limited per user; question ≤ 4,000 chars |
| POST | `/api/resume/upload` | session + CSRF | PDF/DOCX/TXT ≤ `MAX_UPLOAD_MB`; becomes the active resume |
| POST | `/api/resumes/from-text` | session + CSRF | Pasted text as a resume |
| GET | `/api/resumes`, `/api/resumes/{id}`, `/api/resumes/{id}/download` | session | Owner-scoped (other users' ids return 404) |
| PATCH/DELETE | `/api/resumes/{id}` | session + CSRF | Select active / edit text / delete |
| POST/GET/PATCH/DELETE | `/api/roles…` | session (+ CSRF on writes) | Target-role library (max 4) |
| GET | `/api/admin/stats`, `/business`, `/ai-costs`, `/users`, `/users/export`, `/ai-models`, `/stt-models` | admin | Dashboards and management |
| PATCH | `/api/admin/users/{id}`, `/ai-models/{key}`, `/stt-models/{key}` | admin + CSRF | Admins can't demote or deactivate themselves |
| GET | `/api/plans` | public | Active plans with the price checkout charges now (DB-driven) |
| GET | `/api/payments/gateways` | public | Gateways with credentials configured |
| GET | `/api/billing/me` | session | Own current plan, access end, subscriptions, payments |
| POST | `/api/billing/trial` | session + CSRF | One free trial per account (plans with `trial_days > 0`) |
| POST | `/api/payments/checkout` | session + CSRF | Server-priced gateway order |
| POST | `/api/payments/{gateway}/verify` | session + CSRF | Server-side verification of the caller's own order |
| POST | `/api/payments/webhooks/{gateway}` | gateway signature | Razorpay / Cashfree webhooks (raw-body HMAC, deduplicated) |
| GET/POST/PATCH | `/api/admin/plans…`, `/api/admin/subscriptions…`, `/api/admin/payments…`, `/api/admin/users/{id}/billing`, `/api/admin/subscription-audit` | admin (+ CSRF on writes) | Plan management, filters, grant/extend/revoke, reconcile, audit. See [../docs/PAYMENTS.md](../docs/PAYMENTS.md) |
| WS | `/ws/interview?ticket=…` | ticket (or same-origin cookie) | Live transcript, audio, answers; see the docstring in `app/api/websocket.py` |

## Testing

```powershell
cd backend
python -m pytest -q
```

* About 240 tests, roughly 1 minute. Each run gets its own temporary SQLite file,
  so concurrent runs don't block each other.
* **Provider keys are blanked automatically** (`tests/conftest.py`), so the suite
  never calls billed AI, STT or Google APIs, even with real keys in `backend/.env`.
  To run a deliberate live smoke test: `$env:AIA_TESTS_USE_REAL_KEYS=1; python -m pytest -q -k <test>`.
* `tests/test_architecture.py` enforces the layer boundaries (see ARCHITECTURE §2).
* `tests/test_security_hardening.py` holds regression tests for CSRF, session
  revocation, rate limits, CSV injection, WebSocket origin checks, upload limits
  and indexes.

## Database & migrations

* `init_db()` runs on every start: it creates the PostgreSQL database if missing,
  runs `create_all`, then makes **additive, idempotent** changes:
  `_ensure_user_columns` (`username`, `is_admin`, `login_count`,
  `sessions_revoked_at`), `_ensure_resume_columns`, `_ensure_activity_indexes`
  (`(kind, created_at)`), plus the one-off `activity_log` analytics-column cleanup.
* Adding a column: update the model, then add it to the relevant `_ensure_*` helper
  with a dialect-specific type. Record the rollback statement in the PR.
* Rollback statements for the latest changes are in
  [../docs/AUDIT_REPORT.md §5](../docs/AUDIT_REPORT.md#5-schema-change-record).
* Recommended next step: Alembic (versioned migrations with downgrades, applied
  in CI/CD rather than at app start).

## Deployment (Render)

1. Python web service, root `backend/`, build `pip install -r requirements.txt`.
2. Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers`.
3. Environment: `ENVIRONMENT=production`, `SESSION_SECRET`, `DATABASE_URL`,
   `CORS_ORIGINS=https://www.oyeinterview.com,https://oyeinterview.com`,
   `FRONTEND_URL`, `BACKEND_URL`, `GOOGLE_REDIRECT_URI=https://www.oyeinterview.com/api/auth/google/callback`,
   provider keys, `SMTP_*`, `ADMIN_PASSWORD`, `TRUSTED_PROXY_COUNT`.
4. Run **one worker** unless the rate limiter and interview context have moved to
   Redis (see ARCHITECTURE §7).

## Security practices

* Never log passwords, tokens, resume text or question text. Log ids and lengths.
* New mutating route: depend on `require_user_csrf` (or `require_admin` +
  `require_csrf`). New read route: `require_user`, and scope every query by `user.id`.
* Blocking work (DB, hashing, parsing, SMTP) belongs in a `def` handler or behind
  `run_in_threadpool`, never directly in an `async def`.
* Validate input with Pydantic `Field(max_length=…)`; reject rather than truncate
  at the REST layer.
* Keep `AIInterviewResponse` strict-JSON. Protocol changes need explicit approval
  (see `AGENTS.md`).

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| "AI not configured" even though keys are in `.env` | The file must be `backend/.env`. Check the `llm_configured` startup log line. |
| Google sign-in returns `?error=google_state` | The callback ran on a different origin than the login start. Point `GOOGLE_REDIRECT_URI` at the **frontend** origin (`…/api/auth/google/callback`). |
| `Error 400: redirect_uri_mismatch` | Register the exact `GOOGLE_REDIRECT_URI` in Google Cloud Console. |
| Logged out after every deploy | `SESSION_SECRET` isn't set in production (an ERROR at startup says so). |
| 403 "Invalid or missing CSRF token" | The client didn't echo `aia_csrf` in `X-CSRF-Token`. Call `GET /api/auth/csrf` after a reload. |
| WebSocket closes with 1008 | Missing or expired ticket (fetch one right before connecting), or cookie auth from an origin not in `CORS_ORIGINS`. |
| 429 on login for everyone | A proxy collapsed client IPs. Check `TRUSTED_PROXY_COUNT` against the `X-Forwarded-For` shape. |
| Tests hang | An old `pytest` process may hold a DB file. Each run now uses its own temp DB; kill any stray runs. |
