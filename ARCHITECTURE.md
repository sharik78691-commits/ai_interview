# Architecture — OyeInterview (AI Interview Assistant)

A modular monolith: one FastAPI service, one Angular 20 SPA, and an Electron
desktop shell that reuses the same SPA build. Clients talk to the API over REST
(`/api/*`) and a single WebSocket (`/ws/interview`). Persistent data lives in
SQLAlchemy 2.x: PostgreSQL in production, SQLite locally. Live-interview state
is kept in memory on purpose, so transcripts are never stored.

> Companion docs: [README.md](README.md) (setup and operations),
> [backend/README.md](backend/README.md), [frontend/README.md](frontend/README.md),
> [frontend/electron/README.md](frontend/electron/README.md) (desktop),
> [docs/AUDIT_REPORT.md](docs/AUDIT_REPORT.md) (security and performance audit).

## 1. System context

```
                    ┌──────────────────────────── Vercel (static) ─────────────────────┐
 Browser ──HTTPS──▶ │ www.oyeinterview.com  — Angular SPA (dist/frontend/browser)      │
   │                │ rewrites: /api/*  → Render backend   (same-origin cookies)       │
   │                │           /*      → index.html       (SPA routing)               │
   │                └───────────────────────────────┬──────────────────────────────────┘
   │                                                │ /api/* (proxied)
   │  wss:// (direct; static hosts can't proxy WS)  ▼
   └────────────────────────────────────────▶ ┌──────────── Render ────────────┐
                                              │ FastAPI + uvicorn              │──▶ PostgreSQL
 Desktop app (Electron, file://) ──HTTPS─────▶│  REST /api/*  ·  WS /ws/*       │──▶ LLM providers (Groq, Gemini,
   (REST via www.oyeinterview.com,            └────────────────────────────────┘      OpenRouter, OpenAI, DeepSeek)
    WS direct to Render)                                                          ──▶ STT (Whisper-compatible)
                                                                                  ──▶ Google OAuth / SMTP
```

* **Same-origin REST.** Vercel proxies `/api/*`, so the session cookie belongs to
  the site origin. Google OAuth callbacks must come back to that same origin,
  otherwise the state cookie is missing.
* **Cross-origin WebSocket.** The socket goes straight to Render. Browsers don't
  send cookies to a cross-origin socket, so the client first gets a 60-second
  signed **ticket** from `POST /api/auth/ws-ticket` (a same-origin call where the
  cookie is present) and passes it as `?ticket=`.

## 2. Backend modules (`backend/app`)

| Package | Responsibility |
|---------|----------------|
| `api/` | Thin routers: `auth`, `resume`, `roles`, `interview`, `websocket`, `admin`, `ai_models`, `health`. Identity always comes from the session, never from client-supplied ids. |
| `auth/` | `security` (signed session, CSRF and WS-ticket tokens, cookie attributes, revocation check), `dependencies` (`require_user`, `require_user_csrf`, `require_admin`, `require_csrf`), `service` (user lookup, registration, Argon2id auth, Google linking, reset tokens, admin seed), `oauth` (Google OIDC code flow plus JWKS validation), `password`. |
| `core/` | `config` (all settings from env, logged at startup with secrets redacted), `middleware` (security headers, request log with request id), `ratelimit` (in-process fixed window, per IP and per subject), `http` (shared pooled `httpx` client), `logging`. |
| `db/` | Engine and session factory, plus `init_db()`: creates the database if missing, `create_all`, then idempotent additive column and index migrations. |
| `models/` | ORM tables (`User`, `PasswordResetToken`, `RevokedSession`, `ResumeRecord`, `TargetRole`, `ActivityLog`, `AIModelConfig`) and Pydantic schemas (`AIInterviewResponse` strict-JSON contract, request models with length caps). |
| `providers/` | `payments/` (independent Razorpay / Cashfree / dev-mock adapters behind `PaymentGateway`; signature + API verification, no DB access), `llm/` (model registry with an allowlist and admin overrides, an OpenAI-compatible streaming provider, and a mock), `stt/` (Whisper-compatible provider with hallucination filtering, and a mock). |
| `services/` | `ai_service` (answer orchestration; provider chosen through an injectable factory), `interview_context` (per-user context store, the one place to swap in Redis), `question_service` (question-completeness buffer), `resume_service` (PDF/DOCX/TXT extraction and heuristic parsing), `model_config_service` (admin model flags, one default per catalogue), `subscription_service` / `payment_service` (plans, promo pricing, stacking periods, verified activation, webhooks — see docs/PAYMENTS.md), `activity` (fire-and-forget event and LLM-usage log), `business_analytics`, `ai_cost`, `email_service`. Services never import FastAPI. |
| `repositories/` | `owned_library` (generic per-user library: owner-scoped get/list, one active row, prune to N, delete-and-promote), used for both resumes and target roles. |

**Layering rules** are enforced by `tests/test_architecture.py`, which parses every
import: routers never import routers; nothing below `api/` imports it; `db/` is
schema-only; repositories hold queries only; providers are adapters (DB reads
limited to the two model-override registries); `core/` imports nothing above it.

**Concurrency rule.** Handlers that only do blocking work (DB, Argon2, SMTP) are
plain `def`, which FastAPI runs in its threadpool. `async def` is reserved for
handlers that await network I/O; any blocking step inside them goes through
`run_in_threadpool`. The event loop is shared with the live-answer WebSocket, so
one slow call there delays every user's answers.

## 3. Request flows

**REST (authenticated, mutating):**
`HttpClient` → `authInterceptor` (adds `withCredentials` and `X-CSRF-Token` from the
readable `aia_csrf` cookie) → Vercel `/api` rewrite → `RequestLoggingMiddleware` →
`CORSMiddleware` → `SecurityHeadersMiddleware` → router → `require_user_csrf`. That
dependency verifies the signed `aia_session`, checks the logout deny-list, re-loads
the user (must be active), compares the session's `iat` with
`users.sessions_revoked_at`, then checks the double-submit CSRF token → handler.

**Live interview (WebSocket):**
SPA → `POST /api/auth/ws-ticket` → `wss://…/ws/interview?ticket=…` → ticket checked
(signature, 60 s TTL, user still active) **before** `accept()` → per-connection
`QuestionBuffer` and `AIService` → messages: `transcript` / `force` / binary audio +
`audio_flush` → STT → question detection → de-duplicated answer queue (one in-flight
answer per normalised question) → streamed `ai_guidance` deltas → final validated
`AIInterviewResponse`. Cookie auth is accepted only from a `CORS_ORIGINS` origin
(protection against cross-site WebSocket hijacking).

**Google sign-in:** `/api/auth/google/login` sets a short-lived state cookie
(`SameSite=None; Secure` in production so it survives the cross-site return) →
Google → `/api/auth/google/callback` checks the state, exchanges the code, verifies
the `id_token` (JWKS signature, `iss`, `aud`, `exp`, verified email) → creates or links
the user → session cookies → redirect to `/dashboard`.

## 4. Data model

```
users ─┬─< resumes            (user_id FK, ON DELETE CASCADE; file_data deferred blob)
       ├─< target_roles       (user_id FK, ON DELETE CASCADE)
       └─< password_reset_tokens (user_id FK; only SHA-256 of the token stored)
users ─┬─< payments           (plan snapshot + amount_minor; UNIQUE (gateway, order_id/payment_id))
       ├─< user_subscriptions (access periods; source payment|manual|trial; UNIQUE payment_id)
       └─< subscription_audit_logs (grant/extend/revoke/purchase/plan changes, actor + reason)
subscription_plans ─< payments, user_subscriptions   (ON DELETE RESTRICT — plans are deactivated, never deleted)
payment_webhook_events          (UNIQUE (gateway, event_id) — webhook idempotency)
revoked_sessions               (SHA-256 of logged-out session tokens, expires_at indexed)
activity_log                   (kind, user_id, created_at + LLM usage columns; no FK, append-only)
ai_model_config                (admin overrides keyed by model key — enabled/default/priority)
```

| Table | Key constraints / indexes | Notes |
|-------|---------------------------|-------|
| `users` | `email` unique, `username` unique, `google_subject_id` unique | `password_hash` NULL for Google-only accounts. `sessions_revoked_at` gives "sign out everywhere". |
| `resumes` | `user_id`, `user_email`, `created_at` indexed | Max 4 per user (oldest pruned). Exactly one `is_active`. |
| `target_roles` | `user_id`, `user_email`, `created_at` indexed | Max 4 per user. Exactly one `is_active`. |
| `activity_log` | `kind`, `user_id`, `created_at`, **`(kind, created_at)`** | The one growing table, two rows per answered question. |
| `revoked_sessions` | `token_hash` unique, `expires_at` indexed | Expired rows pruned on each logout. |

**Migrations.** `init_db()` runs on every start. It creates missing tables, then
adds missing columns and indexes idempotently (`_ensure_user_columns`,
`_ensure_resume_columns`, `_ensure_activity_indexes`, …). It never drops data,
except the deliberate `activity_log` analytics-column cleanup. Moving to Alembic is
the recommended next step (see the audit report).

## 5. Desktop application (`frontend/electron`)

```
Electron main process (main.js)
 ├─ Main window  — loads dist/frontend/browser/index.html (#/interview, hash routing)
 │    preload.js → window.electronAPI {invisible.*, setOpacity, screenshot.*}
 │    contentProtection ON (hidden from screen share), tray-only, Ctrl+Shift+A toggle
 ├─ Copilot window — copilot.html, always-on-top, contentProtection ON
 │    copilot-preload.js → window.copilotAPI {onUpdate, getState, close}
 ├─ screenshot/  — capture (desktopCapturer), key-store (safeStorage/DPAPI, no
 │    plaintext fallback, keys never returned to the renderer), analysis (vision API
 │    call from the main process), image-validation, model-config
 └─ session hooks — attaches the aia_csrf cookie as X-CSRF-Token for *.oyeinterview.com/api/*
```

* **Isolation:** `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`.
  Popups are denied (https links open in the system browser). Every IPC channel
  checks which window sent it.
* **Known gap:** `webSecurity: false` is still needed for `file://` plus
  cross-origin API calls. The fix (a custom `app://` protocol) is in the audit report.
* **Environment:** `ng build --configuration electron` swaps in
  `environment.electron.ts` (absolute API/WS URLs, `electron: true`). All
  desktop-only UI stays behind `environment.electron`.

## 6. Security boundaries

| Boundary | Control |
|----------|---------|
| Browser ↔ API | HttpOnly signed session cookie (no tokens in JS storage), double-submit CSRF on every mutation, explicit CORS allowlist, security headers (CSP `default-src 'none'` on API responses, `frame-ancestors 'none'`, HSTS in production). |
| Identity | Argon2id passwords, generic login and forgot-password errors (no account enumeration), timing equalisation, session re-validated on every request, logout deny-list, revocation on password reset. |
| Abuse | Per-IP and per-account auth limits, per-user AI and upload limits, input length caps (question 4k, resume/JD 500k), upload size checked while streaming, WS clip size and duration caps. |
| Data isolation | Every resume, role and context query is scoped by the session user id. Unknown and foreign ids both return 404. Admin routes need `is_admin`, and admins can't demote themselves. |
| Secrets | Provider keys exist only in backend env (never in API responses or the browser). Desktop vision keys are encrypted with OS key storage. Reset tokens and logged-out sessions are stored only as SHA-256 hashes. |
| Logs | No passwords, tokens, resume text or question text. DSN passwords are redacted. Reset links are never logged in production. |

## 7. Scalability plan

| Stage | Change |
|-------|--------|
| Now (1 instance, 1 worker) | In-memory rate limiter and per-user interview context. Pooled outbound HTTP client. Indexed admin range scans. |
| 2+ workers / instances | Move the rate limiter and `_contexts` to Redis (or use sticky sessions). Set `SESSION_SECRET` (required: all workers must share it). Set `TRUSTED_PROXY_COUNT`. |
| Growing `activity_log` | Daily rollup table for the admin dashboards. Partition or archive rows older than 12 months. |
| Heavy resume traffic | Move file blobs to object storage (S3/R2) and keep only a key in `resumes`. Run extraction in a background worker. |
| Schema evolution | Alembic migrations with downgrade scripts, applied in the deploy pipeline instead of at startup. |

## 8. Key flows (detail)

**Prepare:** `POST /api/interview/prepare {resumeText, jobDescription}`
validates length → stores in the caller's per-user context bucket
(`_contexts[user_id] = {resume, job, length}`) → `{status:"ready"}`.
Angular also persists both strings + parsed resume in `localStorage`.

**Live loop:** mic → `AudioService.getUserMedia` → `WebSpeechProvider`
(interim + final) → `TranscriptionService.transcript$` → UI append +
`QuestionDetectorService` (debounce ~1200ms; `?`/wh-word/length heuristics)
→ `InterviewWsService.sendTranscript` → server `QuestionBuffer.push`
→ question? → `AIService.analyze(question)` with `_context` resume+job →
`LLMProvider.analyze_question` → validated `AIInterviewResponse` →
WS `{type:"ai_guidance", data}` → guidance card + history. WS down →
direct `POST /api/interview/analyze` fallback; backend down → `DemoService`
on-device guidance so the UI stays demonstrable.

**Question detection (both sides, same rules):** fragments accumulate until a
flush: text ending in `?`, or >120 pending chars, or debounce expiry; `is_question`
requires a `?` (≥3 words) or a leading wh-word/modal with >20 chars and ≥4 words.
Interviewer-audio clips are already cut at pauses, so `push_complete()` treats any
substantial utterance (≥4 words or ≥25 chars) as a finished prompt. This avoids an
LLM call per partial transcript.

## 9. Logging & error tracking

Errors are logged on both sides so a production failure is diagnosable without
reproducing it locally.

**Backend** (`app/core/logging.py`):
- One line per request (method, path, status, duration, `origin`, request id) via
  `RequestLoggingMiddleware`; 4xx → WARNING, 5xx → ERROR, unhandled → traceback.
- `error.log` in `backend/logs/` **always** captures ERROR+ (rotating 5MB × 3),
  so errors survive restarts. `LOG_TO_FILE=1` additionally writes every line to
  `app.log`. `LOG_LEVEL`/`LOG_FORMAT` (text|json) control verbosity/shape.
- Endpoints log decision inputs (resume upload size/type, analyze question length,
  OAuth state host, cookie presence) so "works locally, fails in prod" issues are
  visible. Config is logged once at startup, redacting DB passwords.

**Frontend**:
- `errorLoggingInterceptor` logs every failed HTTP call (`[http] 502 POST /api/…`)
  so it pairs with the backend request-id line.
- `GlobalErrorHandler` (`ErrorHandler`) captures uncaught Angular errors.
- `InterviewWsService` logs socket close codes/reasons, server `error` frames and
  malformed messages.

## 10. Key technical decisions

- **Accounts and libraries in Postgres/SQLite, interview state in memory:** `users`,
  `password_reset_tokens`, `revoked_sessions`, `resumes`, `target_roles`,
  `activity_log` and `ai_model_config` live in SQLAlchemy 2.x
  (SQLite locally, PostgreSQL via `DATABASE_URL`; database + tables are
  auto-created on startup). Live-interview state stays an in-memory seam =
  per-user `_contexts` dict server-side + `history$` client-side, so the WS schema never
  touches the DB.
- **Browser STT for the candidate mic, server STT for meeting audio:** the Web
  Speech API only accepts microphone input, so the candidate's own voice is
  transcribed in-browser via `SpeechToTextProvider`. Meeting/tab audio cannot be
  transcribed locally, so it is streamed to the backend and transcribed by
  `STTProvider` (Whisper). Both interfaces mean desktop system-audio is a new
  provider, not a rewrite.
- **Strict-JSON Pydantic validation:** every AI reply must parse as
  `AIInterviewResponse{question, questionType, answerPoints[1..], star?, codeHint?, example?, keyTakeaways[], followUpQuestions[], responseLength}`; OpenAI path strips code fences and falls back to mock.
- **Demo-first:** mock LLM + scripted `DemoService` sequences keep the whole UI
  testable with zero keys; `demo_mode = !LLM_API_KEY` unless overridden.
- **Server-managed sessions instead of JWT in storage:** an HttpOnly signed
  cookie can't be read by injected script. It's re-validated against the DB on
  every request, so deactivation, logout and password reset take effect at once.
- **Ticketed WebSocket:** a 60-second, single-purpose signed ticket (its own salt)
  solves cross-origin socket auth without loosening cookie policy.
- **Lazy routes with selective preloading (web):** only landing and auth pages are
  in the initial bundle. Dashboard and interview chunks preload after first paint.

## 11. Interviewer audio (meeting / tab) vs. candidate mic

Two deliberately independent audio pipelines, because the app is used by the
interviewee and must never feed the candidate's own voice to the AI:

```
Candidate mic ──> AudioService.getUserMedia ──> WebSpeechProvider (browser STT)
                     └── setMuted()/toggleMute() (tracks.enabled = false)
                                  │
                                  └──> NEVER used for interviewer questions

Meeting/tab ────> InterviewerAudioService.getDisplayMedia({audio})
                     AudioContext graph
                       ├─ AnalyserNode      (level meter, fallback path)
                       └─ ScriptProcessor   ──> raw Float32 PCM
                                                   │
                     silence detection ends clip ──┤
                                                   ▼
                                        wav-encoder.ts  (downmix + 16 kHz mono)
                                                   │
                                                   ▼
                                  16 kHz mono PCM WAV bytes  (no container)
                                                   │
FastAPI /ws/interview ──> sniff magic bytes ─> correct the declared mime
                                                   │
                                            STTProvider (Whisper)
                                                   │
                                            QuestionBuffer.push_complete()
                                                   └─> AIService.analyze()  [same AI path]
                                                        └─> {ai_guidance} ──> existing card
```

Why raw PCM instead of a container: `MediaRecorder` emits a live WebM with no
duration, and fragments it while recording. Stitched partially, the result is an
invalid file (`HTTP 400 could not process file`); its stop/restart lifecycle also
produced two uploads per question (`429`). Encoding samples ourselves removes the
container entirely, and the backend still sniffs the magic bytes so any client
that does send a container gets labelled correctly.

Why server-side STT: the Web Speech API accepts microphone input only, so tab /
meeting audio cannot be recognised in the browser. Clips are cut at pauses
(silence detection) so STT gets short clean snippets rather than a whole
meeting recording.

Extension seams for the desktop phase:
- New `STTProvider` implementation (Deepgram, local Whisper, desktop audio) —
  the WebSocket contract (`audio_flush{mimeType}` → `stt_transcript`) does not change.
- New `SpeechToTextProvider` for system audio — untouched UI.
- Meeting integrations can simply post `{type:"interviewer_question"}` or stream
  their own audio through the same messages; no AI or layout changes.
- `sniff_audio()` and `MIME_BY_FORMAT` cover wav/webm/mp4/ogg/flac/mp3, so a new
  container only needs an entry there.

## 12. Extension seams

1. New `SpeechToTextProvider` (e.g. desktop audio) or server `STTProvider`: the
   transcript and WebSocket contracts stay the same.
2. Meeting integrations post `{type:"transcript"}` / `{type:"interviewer_question"}`
   to the same WebSocket endpoint.
3. New LLM providers: add a `ModelSpec` to `providers/llm/registry.py`. The
   allowlist, admin overrides and usage logging apply automatically.
4. Payments and plans: new FastAPI dependencies plus Angular route guards. No
   changes to the core flow.
