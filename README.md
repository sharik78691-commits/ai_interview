# OyeInterview — AI Interview Assistant

Real-time, resume-aware interview guidance. Upload a resume and a target job
description, then get concise AI answer points (STAR stories, code hints,
follow-ups) as interview questions are detected from live microphone or
meeting-audio transcription.

Delivered as a **web app** (www.oyeinterview.com) and a **Windows/macOS desktop
app** (Electron tray app with screen-share-invisible windows).

| Document | Contents |
|----------|----------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, request flows, data model, desktop architecture, security boundaries, scalability plan |
| [backend/README.md](backend/README.md) | API setup, environment variables, API reference, tests, migrations, deployment |
| [frontend/README.md](frontend/README.md) | Angular app: development, environments, build, performance, Vercel |
| [frontend/electron/README.md](frontend/electron/README.md) | Desktop app: development, packaging, security model |
| [docs/AUDIT_REPORT.md](docs/AUDIT_REPORT.md) | Latest security/performance audit, verification results, residual risks |
| [backend/.env.example](backend/.env.example) | Every configuration variable with safe placeholders |

## Repository layout

```
backend/            FastAPI service (REST + WebSocket), SQLAlchemy models, providers, tests
frontend/           Angular 20 SPA (web) — also the desktop renderer
frontend/electron/  Electron main process, preloads, screenshot analysis
docs/               Audit report and supporting documents
vercel.json         Vercel build + /api, /ws rewrites to the Render backend
```

## Tech stack

- Frontend: Angular 20 (standalone, signals), TypeScript, RxJS, hand-written CSS
- Backend: Python 3.10+, FastAPI, WebSocket, Pydantic v2, SQLAlchemy 2.x
  (SQLite locally, PostgreSQL in production)
- Auth: HttpOnly signed session cookie + double-submit CSRF, Argon2id, Google OIDC
- Speech: browser Web Speech API (candidate mic) + server-side Whisper-compatible
  STT (meeting/tab audio)
- AI: multi-provider LLM registry (Groq, Gemini, OpenRouter, OpenAI, DeepSeek)
  with a strict-JSON `AIInterviewResponse` contract and a mock/demo fallback
- Desktop: Electron (content-protected windows, tray, encrypted key storage)
- Hosting: Vercel (SPA) → Render (API) → PostgreSQL

## Quick start (Demo mode, no keys)

**Backend**

```powershell
cd backend
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Health: `GET http://localhost:8000/api/health` → `{"status":"ok","demo_mode":true,...}`
Docs: `http://localhost:8000/docs` (development only; hidden in production unless
`ENABLE_API_DOCS=true`)

**Frontend**

```powershell
cd frontend
npm install
npm run start   # http://localhost:4200 (proxied /api + /ws -> :8000)
```

Flow: Landing → Get Started → **Login / Sign Up** → Dashboard (upload resume +
paste JD + Prepare) → Interview (Start, then Demo or speak/type). `Try Demo`
skips the backend. Authentication is required for the dashboard, interview and
settings; the health endpoint stays public.

## Features

- **Resume-aware answers** — upload a PDF/DOCX/TXT resume (or paste text) and a
  job description; every answer is tailored to that stack and role.
- **Live question detection** — interviewer questions are detected from
  microphone transcription (Web Speech API) or shared meeting/tab audio.
- **AI Guidance card** — structured answer points, key takeaways, a real-world
  example, a STAR story for behavioural questions, and a code hint for coding
  questions.
- **Answer depth** — Short / Medium / Long, selectable from the Interview screen
  or Settings; changing it mid-interview re-asks the current question.
- **Adjustable text size** — the AI Guidance font size is user-adjustable
  (12–24 px) and persisted.
- **Dark / light theme** — toggled from the navbar or Settings, persisted in
  `localStorage`.
- **Session history** — earlier questions in the session are kept as collapsible
  accordions.
- **Manual fallback** — paste or type a question and press **Generate Answer**;
  also **Clear** and **Copy Answer**.
- **Demo mode** — a scripted demo runs entirely on-device with no backend or keys.

## Real AI mode

1. Copy `backend/.env.example` to `backend/.env`, set `LLM_API_KEY` (+ optional `LLM_MODEL`, `LLM_BASE_URL`).
2. Restart uvicorn. `demo_mode` becomes `false`; `/api/interview/analyze` and the
   WebSocket path call the configured OpenAI-compatible `chat/completions` endpoint
   with a strict-JSON system prompt, falling back to the mock provider on failure.
3. Keys never leave the backend — Angular only talks to `/api/*` and `/ws/interview`.

## AI model selection

Users can choose among four text-generation models; transcription (Whisper) is
unaffected and has no selector. The default is **GPT-OSS 120B through Groq**.

| Selector key | Provider | Model ID | Endpoint |
|---|---|---|---|
| `groq-gpt-oss` (default) | Groq | `openai/gpt-oss-120b` | `https://api.groq.com/openai/v1` |
| `gemini-flash` | Google | `gemini-2.5-flash` | `https://generativelanguage.googleapis.com/v1beta/openai` |
| `qwen-coder` | OpenRouter · Qwen | `qwen/qwen3-coder:free` | `https://openrouter.ai/api/v1` |
| `openrouter-free` | OpenRouter | `meta-llama/llama-3.3-70b-instruct:free` | `https://openrouter.ai/api/v1` |

Model IDs verified Oct 2026 against: Groq console model docs
(`console.groq.com/docs/models`, `openai/gpt-oss-120b` featured), Google AI
OpenAI-compatibility docs (`ai.google.dev/gemini-api/docs/openai`, base URL
`.../v1beta/openai`), and OpenRouter's free-model collection (`:free`-suffixed
models such as `qwen/qwen3-coder:free`). Free-model availability rotates — if a
`:free` model is retired, set `QWEN_MODEL` / `OPENROUTER_MODEL` in `backend/.env`
to another `:free` model from `openrouter.ai/models` (no code change needed).

Setup:

```env
GROQ_API_KEY=        # optional: falls back to LLM_API_KEY
GEMINI_API_KEY=      # Google AI Studio key for Gemini Flash
OPENROUTER_API_KEY=  # serves both Qwen and the OpenRouter fallback
DEEPSEEK_API_KEY=    # DeepSeek V4.1 Flash + V4 Pro (chat needs account balance)
```

- DeepSeek runs on its official OpenAI-compatible endpoint
  (`https://api.deepseek.com/v1`). Model ids come from `GET /models`:
  `deepseek-flash` (DeepSeek-V4.1 Flash) and `deepseek-v4-pro`
  (DeepSeek-V4 Pro). An empty balance rejects generation with HTTP
  402 `Insufficient Balance` even though the key authenticates.

- A model is selectable only when its provider key is set AND an admin has not
  disabled it. Keys without configuration show as "unavailable" in the UI.
- `GET /api/ai/models` exposes names, providers, availability, free-tier flags
  and capabilities — never keys or endpoints.
- The choice is sent per request (`model` on REST + every WS question message)
  and persisted in browser settings; admins manage enable/default/labels at
  `GET/PATCH /api/admin/ai-models` (admin panel → AI models).
- Usage (provider, model, latency, provider-reported tokens, error category) is
  logged per call to the existing `activity_log` table (`kind = llm_usage`).

Known free-tier limits (approx, provider-enforced, subject to change):
Groq ~30 req/min + daily caps; Google AI Studio free tier has per-minute/per-day
quotas per model; OpenRouter `:free` models are rate-limited (tens of
req/min) and may be slower. The backend retries 429/5xx briefly with backoff,
then shows template guidance with a notice and a **Retry** button — it never
silently switches models.

## Interviewer audio detection (meeting / browser tab)

The app is used by the **interviewee**, so questions must come from the
interviewer, not from the candidate's own microphone.

Two separate audio sources exist and never mix:

| Source | Service | Used for |
|---|---|---|
| Your microphone | `AudioService` + Web Speech API | your own voice (notes, practice) |
| Meeting / browser tab | `InterviewerAudioService` → server STT | **interviewer question detection** |

How it works:

1. Click **Select meeting audio** and choose the meeting tab/window. The browser
   asks you to allow audio — tick **"Also share tab audio"** (Chrome/Edge).
2. Short clips are recorded and cut automatically when the interviewer pauses
   (silence detection via `AudioContext` energy check).
3. Each clip is streamed to the backend over the existing WebSocket and
   transcribed by the STT provider (Whisper).
4. The recognised text goes through the **same** `QuestionBuffer` detection and
   the **same** `AIService.analyze()` used by every other path, so the answer
   appears in the existing AI Guidance card. No second AI system exists.

Requirement: browser tab audio + speech-to-text. Browsers cannot transcribe tab
audio locally, so transcription is server-side:

```env
STT_API_KEY=          # optional: falls back to LLM_API_KEY
STT_MODEL=whisper-large-v3-turbo   # Groq; use whisper-1 for OpenAI
STT_BASE_URL=         # optional: defaults to LLM_BASE_URL
```

Check `GET /api/health` → `stt_configured: true`.

Manual fallback (always available): paste or type the question and press
**Generate Answer** — same AI function, same answer card. Also **Clear** and
**Copy Answer** (copies points, takeaways, example, STAR and follow-ups).

Status messages shown in the UI: `Select meeting audio` → `Listening for the
interviewer's question…` → `Question detected` → `Generating answer…` →
`Answer ready`, plus `Audio permission was denied` and `Browser audio capture is
unavailable. Please paste the question manually.`

> Chrome/Edge on `localhost` or HTTPS. Safari/Firefox support for
> `getDisplayMedia` audio varies; the manual fallback always works.

### Why the app cannot read Teams/Meet audio by itself

Products such as Final Round AI are **desktop apps**: they hook the operating
system audio mixer (WASAPI loopback on Windows, ScreenCaptureKit on macOS) and
therefore hear every meeting app with zero user action. A web page has no such
access — the only audio a website may receive is what the user explicitly shares
through the browser. That is why this web build uses **tab/window sharing with
audio** instead of silent system capture.

| Capability | Desktop app | This web build |
|---|---|---|
| Hears Teams/Meet/Zoom with no picker | Yes (OS mixer) | No |
| Hears the selected tab with audio | Yes | **Yes — user picks the tab** |
| Separate candidate mic mute | Yes | Yes |

If OS-level capture is wanted later, wrap this frontend in a thin Electron
shell: add a `SystemAudioProvider` and forward the stream into the existing
`/ws/interview` binary frame. No AI, API or UI change needed.

### Troubleshooting

| Message | Cause | Fix |
|---|---|---|
| `Browser audio capture is unavailable. Please paste the question manually.` | Backend has no STT provider | Start the server from `backend/` **or** check `backend/.env` has `STT_MODEL=whisper-large-v3-turbo`. `GET /api/health` must show `stt_configured: true` |
| `Tab audio sharing needs a secure address…` | Opened over a LAN IP (`http://192.168.x.x`) | Use `http://localhost:4200` or HTTPS |
| `This browser cannot share tab audio…` | Safari/Firefox, or no `getDisplayMedia` | Use Chrome or Edge on desktop |
| `No audio track was shared…` | "Also share tab audio" not ticked | Re-pick the tab and tick the checkbox |
| `Audio permission was denied` | User cancelled / blocked the picker | Allow the tab, then press the button again |
| `Speech-to-text model '…' is not available on …` | Wrong `STT_MODEL` for the provider | Groq → `whisper-large-v3-turbo`, OpenAI → `whisper-1` |
| `The recorded audio could not be decoded` | Broken or partial clip | Fixed in code — clips are re-encoded as 16 kHz WAV before upload. If it persists, re-share the tab |
| `Speech-to-text rate limit reached` | Too many clips in a burst | Handled automatically: 3 attempts with backoff (honours `Retry-After`), one transcription in flight at a time |

`backend/.env` is loaded by absolute path, so the server can be started from any
working directory without losing the API keys.

### Why clips are converted to WAV

Three separate things were breaking transcription:

| Symptom | Cause | Fix |
|---|---|---|
| `HTTP 400 could not process file` | Sending a partial container (a stopped/restarted `MediaRecorder` emits headerless fragments) | Clips are now encoded straight from **raw PCM** — no container at all |
| `HTTP 400 could not process file` | WAV bytes labelled `audio/webm` (or the reverse) | The backend **sniffs the magic bytes** and corrects the label before uploading |
| `rate limit reached` | Two requests per question (the recorder stop/restart race) | One recorder, one clip per sentence, one request in flight, 3 retries with backoff |

How capture works now:

1. `getDisplayMedia({audio:true})` shares the meeting/tab. Permission is requested
   only from a user click.
2. A `ScriptProcessor` on that stream hands us raw PCM. The audio graph ends in a
   **muted gain**, so the meeting is not played back a second time.
3. Silence detection (RMS on the same samples) ends the clip when the interviewer
   pauses; a 15 s cap handles uninterrupted talking. Silence-only clips are never
   uploaded — they only burned quota.
4. `wav-encoder.ts` downmixes, resamples to **16 kHz mono PCM** and writes a RIFF
   WAV. Seekable, universally accepted, exactly Whisper's native rate.
5. The backend reads `RIFF…WAVE` (or EBML/ftyp/OggS…) and, if the declared mime
   disagrees, overwrites it with the correct one. Unreadable bytes are rejected
   locally with a clear message instead of being sent to the provider.
6. Fallbacks: if `ScriptProcessor` is unavailable we decode `MediaRecorder` output
   with `decodeAudioData`; if that fails too, the original bytes are sent rather
   than losing the question.

## Browser microphone permissions

- Use Chrome/Edge on `http://localhost` or HTTPS; grant mic access on first Start.
- If blocked: the app shows `Microphone permission denied` and you can still type
  questions manually or use Demo mode.
- `Speech recognition unavailable` appears where the Web Speech API is missing.

## Authentication

The app supports exactly two login methods: **Google OAuth** and
**email + password**. Authentication is a **server-managed session** carried in
a signed, `HttpOnly` cookie — no token is ever stored in `localStorage` or
`sessionStorage`.

### Endpoints

| Method | Path | Auth | Body | Returns |
|---|---|---|---|---|
| POST | `/api/auth/register` | — | `{name, email, password}` | `{authenticated, user}` + session cookie |
| POST | `/api/auth/login` | — | `{email, password}` | `{authenticated, user}` + session cookie |
| POST | `/api/auth/logout` | session + CSRF | — | `{message}` (clears the session) |
| GET | `/api/auth/me` | optional | — | `{authenticated, user\|null}` |
| GET | `/api/auth/google/login` | — | — | 302 redirect to Google (with `state`) |
| GET | `/api/auth/google/callback` | — | `?code&state` | 302 to `/dashboard` + session cookie |
| POST | `/api/auth/forgot-password` | — | `{email}` | generic `{message}` (no enumeration) |
| POST | `/api/auth/reset-password` | — | `{token, password}` | `{message}` |
| GET | `/api/auth/csrf` | session | — | refreshes the CSRF cookie |

Responses never include password hashes, OAuth tokens or internal security data.

### Password reset emails

Forgot-password actually **sends** the reset link by email (1-hour, single-use
token). Any SMTP host works — configure in `backend/.env` (local) and Render
env vars (production):

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=<app-password>
SMTP_FROM=you@gmail.com   # defaults to SMTP_USER when empty
SMTP_USE_TLS=true
```

For Gmail, create an **App Password** (Google Account > Security > 2-Step
Verification > App passwords) — a normal login password is rejected. With no
`SMTP_HOST` set, mail is disabled and the link goes to the server log only
(local-dev fallback); the API response is identical either way, and a mail
failure never breaks the endpoint. The emailed link points at `FRONTEND_URL`,
so it opens the website's reset page — desktop-app users reset there and sign
back into the app, no installer change needed.

### Protected endpoints

`/api/resume/upload`, `/api/interview/prepare`, `/api/interview/context`,
`/api/interview/analyze` and `/ws/interview` all require an authenticated
session. `GET /api/health` remains public. The WebSocket is authenticated with
the **same** session cookie (checked before the connection is accepted) — there
is no separate WebSocket auth system.

### User isolation

Interview context is stored per authenticated user id (never a client-supplied
id). One user can never read another user's resume, job description, context or
AI answers. Frontend route guards are UX only; the backend derives identity from
the session on every request.

### Resume library

Uploads are stored server-side (`resumes` table: owner + owner email, original
file bytes, extracted text, parsed JSON, active flag) — not just in browser
localStorage — so resumes survive logout and work across devices. A library
holds at most **4 resumes per user**: storing a 5th auto-deletes the oldest. Extraction
reads DOCX tables row-by-row (table layouts no longer lose text) and retries
complex PDFs in layout mode; scanned/image PDFs are rejected with guidance to
upload DOCX/TXT instead of storing a broken fragment:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/resume/upload` | Parse + store; the new upload becomes the active resume |
| GET | `/api/resumes` | Own library, newest first (lean rows, no heavy text) |
| GET | `/api/resumes/{id}` | Full parsed resume + editable source text |
| GET | `/api/resumes/{id}/download` | Original uploaded file |
| PATCH | `/api/resumes/{id}` | `{is_active?}` select active, `{raw_text?}` correct text (re-parses skills) |
| DELETE | `/api/resumes/{id}` | Delete (newest remaining resume becomes active) |

The dashboard's My resumes section lists, activates, downloads and deletes;
interviews keep using the active resume's text through the unchanged prepare
flow. The admin users table shows each account's resume count.

### Target-role library

Mirrors the resume library (`target_roles` table: owner + owner email,
position, job description, active flag). A library holds at most **4 roles
per user**: saving a 5th auto-deletes the oldest.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/roles` | Save position (required, ≤200 chars) + JD (required, ≤2000 chars); becomes the active role |
| GET | `/api/roles` | Own library, newest first |
| GET | `/api/roles/{id}` | Full role with job description |
| PATCH | `/api/roles/{id}` | `{position?, job_description?, is_active?}` select/correct |
| DELETE | `/api/roles/{id}` | Delete (newest remaining role becomes active) |

The dashboard's Saved roles section lists, activates and deletes; the editor
adopts the active role on fresh browsers/devices without overwriting typed
text. Unknown-or-foreign ids answer 404, so users can never touch each
other's resumes or roles.

### Security decisions

- **Passwords**: Argon2id hashing; never logged, never returned. Minimum 8
  chars with a letter and a number.
- **Sessions**: signed `HttpOnly` cookie, `Secure` in production, `SameSite=Lax`,
  server-side re-validation, fresh token on login (session-fixation safe),
  logout clears the cookie.
- **CSRF**: double-submit token (`aia_csrf` cookie echoed in `X-CSRF-Token`) on
  **every** state-changing request — auth, resumes, roles, interview and admin.
- **CORS**: explicit origins only — never `*` with credentials; the same
  allowlist gates cookie-authenticated WebSockets (cross-site WebSocket
  hijacking protection).
- **Rate limiting**: in-process fixed-window limiter on login/register/forgot/
  reset (default 10/min per IP **and** per account), plus a per-user limit on AI
  answers and resume uploads (`AI_RATE_LIMIT_PER_MINUTE`, default 30). Set
  `TRUSTED_PROXY_COUNT` behind proxies so `X-Forwarded-For` cannot be spoofed.
- **Session revocation**: logout deny-lists the token; a password reset signs
  out every existing session.
- **Session secret**: production without `SESSION_SECRET` uses a random
  per-process secret (never the public dev fallback) and logs an ERROR.
- **Security headers**: `X-Content-Type-Options`, `Referrer-Policy`,
  `X-Frame-Options`, `Content-Security-Policy` (`frame-ancestors 'none'`),
  `Permissions-Policy` (allows microphone + display-capture for same-origin),
  and HSTS in production.
- **Google OAuth**: authorization-code flow with `state` protection; the
  `id_token` is validated for issuer, audience, expiry and signature (Google
  JWKS); only verified Google emails are accepted. The client secret never
  leaves the backend.

### Google OAuth setup

1. In [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
   create an **OAuth 2.0 Client ID** (Web application).
2. Add an **Authorized redirect URI** for **every** environment that serves the
   callback. The value must match `GOOGLE_REDIRECT_URI` **verbatim** — scheme,
   host, port and path:
   - Production: `https://ai-interview-1-309j.onrender.com/api/auth/google/callback`
   - Local dev (optional): `http://localhost:8000/api/auth/google/callback`
3. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `GOOGLE_REDIRECT_URI` in
   `backend/.env`. If `GOOGLE_REDIRECT_URI` is left empty it defaults to
   `{BACKEND_URL}/api/auth/google/callback`, so a deployed backend never falls
   back to `localhost`.

> **Important — the callback is served by the backend, not the frontend.**
> The frontend only links to `/api/auth/google/login`; the dev-server proxy
> (`frontend/proxy.conf.json`) and the Vercel rewrites (`vercel.json`) forward
> `/api` to the deployed backend. The redirect URI Google sees is therefore the
> **backend's** public URL. Register that URL in Google Console.

#### Troubleshooting: `Error 400: redirect_uri_mismatch`

Google returns this **before** the callback runs, which means the
`redirect_uri` sent by the backend is not registered on the OAuth client. Check:

- The value of `GOOGLE_REDIRECT_URI` on the **deployed** backend (Render env
  vars), not just your local `backend/.env`.
- That the registered URI matches exactly — `http` vs `https`, a trailing
  slash, `www`, or a different port all cause a mismatch.
- That you edited the **same** OAuth client whose `GOOGLE_CLIENT_ID` the backend
  uses.
- That changes were saved and given a minute to propagate in Google Console.

### Database

A SQLAlchemy 2.x layer backs authentication. SQLite is the default for local
development (zero infrastructure); set `DATABASE_URL` to a PostgreSQL DSN to
use Postgres locally or in production:

```env
# backend/.env
DATABASE_URL=postgresql+psycopg://postgres:PASSWORD@localhost:5432/oyeinterviewdb
```

On startup the backend creates the PostgreSQL **database itself when it is
missing** (SQLite files appear on first connect anyway) and then creates all
tables — a fresh machine only needs a running Postgres server, nothing manual.
The driver ships with the requirements (`pip install -r
backend/requirements.txt` installs `psycopg[binary]`).

`users`: `id, email, password_hash, name, auth_provider, google_subject_id,
email_verified, is_active, created_at, updated_at, last_login_at, username,
is_admin, login_count`.
`password_reset_tokens`: `id, user_id, token_hash, expires_at, used_at,
created_at` (only a SHA-256 hash of the token is stored; single-use + expiring).
`revoked_sessions`: one row per logged-out session (server-side logout).

Google-only accounts have `password_hash = NULL` and `auth_provider = "google"`.
Local accounts have an Argon2id hash and `auth_provider = "local"`. A Google
identity is linked onto an existing local account with the same verified email,
so both login methods share one account (no duplicates).

### Admin panel

An admin login is seeded automatically on startup when `ADMIN_PASSWORD` is set
(local: `backend/.env`, production: host env vars). `ADMIN_EMAIL` (default
`admin@oyeinterview.com`) and `ADMIN_USERNAME` (default `admin`) are
configurable; the password is never hardcoded. Login accepts the email **or**
the username, and every successful sign-in bumps `login_count`.

Admin endpoints (all require an admin session — 401 anonymous, 403 non-admin):

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/admin/stats?from=&to=` | Business dashboard: system status, lifetime totals (users/active/admins/logins), in-range metrics (signups/interviews/questions/AI calls/AI errors), signups chart + AI usage over the selected window (default: last 30 days) |
| GET | `/api/admin/users?q=&page=&page_size=` | Searchable, paginated user list (newest first) |
| PATCH | `/api/admin/users/{id}` | `{is_active?, is_admin?}` — activate/deactivate, promote/demote (self-changes refused) |
| GET | `/api/admin/users/export` | Whole user list as CSV download |
| GET | `/api/admin/ai-models` | Model catalog + provider error stats (no secrets) |
| PATCH | `/api/admin/ai-models/{key}` | `{enabled?, is_default?, display_name?, description?, free_tier?, priority?}` |

Key business events (`user_login`, `user_registered`, `interview_prepared`,
`question_analyzed`) and every AI call (`llm_usage`: provider, model, latency,
provider-reported tokens, error category) append to the single `activity_log`
table via never-failing helpers. The Angular `/admin` page (guarded by
`adminGuard`, hidden from search engines) shows a lean business dashboard —
system health, business totals, a 30-day signup chart, an AI usage table
(calls/errors/latency per model), AI model management, and the user table.
The navbar shows the Admin link only to admins.

### What is deliberately NOT tracked

The previous per-request visit logging, button-click tracking, dwell-time
heartbeats, GeoIP labels, device/user-agent capture and raw-IP hashing were
removed as high-volume noise with no business value — they're neither stored
nor shown anymore.

## API

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/health` | — | `{status, demo_mode, llm_configured, stt_configured, stt_model}` (also aliased at `/health`) |
| POST | `/api/resume/upload` | multipart `file` (.pdf/.docx/.txt ≤10MB) | `{filename, chars, resume: ResumeData, id, is_active}` (stored + made active) |
| POST | `/api/resumes/from-text` | `{filename?, raw_text}` (editor text, ≤500KB) | stored + made active; a downloadable `.txt` is synthesized |
| POST | `/api/interview/prepare` | `{resumeText, jobDescription, responseLength?}` (≥50 chars each) | `{status:"ready", message, resumeChars, jobChars, responseLength}` |
| GET | `/api/interview/context` | — | `{hasContext, resumeChars, jobChars, responseLength, resume, job}` (resume/job truncated to 500 chars) |
| POST | `/api/interview/analyze` | `{question, resumeText?, jobDescription?, responseLength?, model?}` | `AIInterviewResponse` |
| GET | `/api/ai/models` | — | `{models: [{key, provider, displayName, modelId, available, enabled, freeTier, capabilities, isDefault, priority}], default}` (no secrets) |
| GET | `/api/admin/ai-models` | admin only | catalog + default + provider error stats |
| PATCH | `/api/admin/ai-models/{key}` | admin only, `{enabled?, is_default?, display_name?, description?, free_tier?, priority?}` | updated catalog + default |
| WS | `/ws/interview` | `{type:"transcript",text,responseLength?,model?}` / `{type:"force",text,responseLength?,model?}` / `{type:"interviewer_question",text,responseLength?,model?}` / `{type:"set_model",model}` / `{type:"interviewer_audio_start",mimeType?}` / `{type:"interviewer_audio_stop"}` / binary audio frame + `{type:"audio_flush",mimeType?,responseLength?,model?}` / `{type:"reset"}` / `{type:"ping"}` | `{type:"ai_guidance",data,model?,provider?}` / `{type:"ai_stream_start",model,provider}` / `{type:"ai_stream_delta",point}` / `{type:"stt_transcript",text,final}` / `{type:"status",status,message}` / `{type:"notice",scope?,message}` / `{type:"error",message}` / `{type:"pong"}` |

### Streaming answers (WebSocket only)

Live answers stream token-by-token over the existing socket — no new
connection, no client change required to benefit (old clients ignore the two
new frames and just get `ai_guidance` as before):

1. `{type:"ai_stream_start", model, provider}` — which model is generating, live.
2. `{type:"ai_stream_delta", point}` — one completed answer bullet at a time
   (incrementally parsed from the streaming `answerPoints` array).
3. `{type:"ai_guidance", data, ...}` — the final answer, validated by the same
   strict Pydantic schema as the REST endpoint. The preview is provisional and
   is always replaced by this card.

Notes: REST `/api/interview/analyze` stays single-shot (same contract).
If the stream drops mid-answer, the backend makes one single-shot attempt
before template fallback, so users get the real card or a clear error + Retry.
Streamed token counts are not reported by providers mid-stream, so usage rows
for streamed calls carry latency + error category with `NULL` tokens (never
estimated). Same-question duplicates are suppressed before streaming starts.

### WebSocket message flow for interviewer audio

```
browser                                   backend
{interviewer_audio_start}          ───►   mark capturing
<binary webm/opus chunk>          ───►   append to clip buffer
{audio_flush}                     ───►   Whisper STT ─► text
                                           │
        ◄── {stt_transcript, text}         │
        ◄── {status: Question detected}    │ QuestionBuffer.push_complete()
        ◄── {ai_guidance, data}            │ AIService.analyze()  ← existing AI feature
```

## Answer detail (short / medium / long)

The user picks how much the AI writes — from the Interview screen's
**Answer detail** selector or Settings. The level travels to the backend with
every question and changes the system prompt:

| Level | Answer points | Also included |
|---|---|---|
| `short` | 3–4 crisp bullets (≤12 words) | fast mental cue for quick answers |
| `medium` (default) | 5–7 bullets, 1–2 sentences each | 1 short example, 2–3 key takeaways |
| `long` | 8–12 detailed bullets (20–45 words) | senior-engineer framing: definition → mechanism → failure modes → alternatives → production relevance; detailed real-world example; 3–5 takeaways; 3–5 deeper follow-ups |

Changing the selector mid-interview re-asks the current question at the new
depth so you can compare instantly. Backend responses also include
`responseLength`, `keyTakeaways` and `example` fields, validated by Pydantic.
Token budgets scale per level (700 / 1400 / 2000) so long answers stay deep while
still completing quickly on real providers.

## Testing

```powershell
cd backend; python -m pytest -q                                   # ~240 tests, ~1 min, no network
cd ../frontend; npm run build                                     # production build (budget-checked)
npx ng test --watch=false --browsers=ChromeHeadless               # Karma specs
npm run test:electron                                             # desktop main-process modules
```

The backend suite blanks AI/STT/OAuth keys automatically, so it never calls
billed providers even when `backend/.env` holds real keys (opt in with
`AIA_TESTS_USE_REAL_KEYS=1`). Each run uses its own temporary SQLite file.

Backend coverage includes authentication (register, duplicate, hashing, login,
invalid password, logout, session validation, `/api/auth/me`, Google OAuth
validation, expired/invalid reset tokens, rate limiting), protected-API access,
cross-user isolation, WebSocket authentication, resume parsing, question
detection, mock AI depth levels, the REST API, and the interviewer-audio/STT
pipeline. Frontend Karma specs cover the auth service, auth guard, demo service,
question detector, resume service, transcription service and app shell.
External AI/STT calls are mocked or fall back in all tests.

## Privacy

Resume and interview context are processed only to provide interview assistance.
Interview context is kept in server memory, **isolated per authenticated
user** (keyed by user id, never a client-supplied id), plus browser
`localStorage`. The database stores accounts, the user's own resume library
(original file + extracted text, max 4 per user) and target roles (max 4), and
a lean activity log (event kind, user id, time, LLM usage) — **never
transcripts or questions**. Full resume text, questions and transcripts are not
logged — only lengths and error summaries. Passwords are never logged and never
returned; only Argon2id hashes are stored.

## Limits → roadmap

```
Shipped: web app, auth (Google + email/password), resume/role libraries,
         multi-model AI, meeting-audio STT, admin panel, desktop app
Next   : see docs/AUDIT_REPORT.md §4 (history purge, Electron upgrade + app://
         protocol, email verification, Alembic, Redis for multi-worker)
  → Zoom/Meet/Teams/Webex integrations
  → Persistent history, payments
```

## Deployment overview

| Component | Host | Build / start | Notes |
|-----------|------|---------------|-------|
| SPA | Vercel | `npm ci && npm run build` → `dist/frontend/browser` | `vercel.json` rewrites `/api/*`, `/ws/*` to Render and `/*` to `index.html` |
| API | Render | `pip install -r requirements.txt` → `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers` | Set `ENVIRONMENT=production`, `SESSION_SECRET`, `DATABASE_URL`, `CORS_ORIGINS`, `FRONTEND_URL`, `GOOGLE_REDIRECT_URI` (frontend origin), provider keys, `TRUSTED_PROXY_COUNT` |
| Database | PostgreSQL | auto-created tables + additive migrations at startup | see backend/README.md → Database & migrations |
| Desktop | GitHub Release / CDN | `cd frontend && npm run dist:win` | see frontend/electron/README.md |

## Troubleshooting (quick index)

- Backend config, OAuth, CSRF, WebSocket 1008, rate limits → [backend/README.md#troubleshooting](backend/README.md#troubleshooting)
- Production logout, live answers, mic permissions → [frontend/README.md#troubleshooting](frontend/README.md#troubleshooting)
- Desktop black window, hotkey, screenshot key → [frontend/electron/README.md#troubleshooting](frontend/electron/README.md#troubleshooting)
- Interviewer audio / STT → [Troubleshooting](#troubleshooting) above
