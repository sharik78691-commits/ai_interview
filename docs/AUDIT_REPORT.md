# Security, Performance & Architecture Audit — oyeinterview.com

**Date:** 2026-10-19 · **Branch:** `dev_new_10-06-20266` · **Scope:** FastAPI backend,
Angular 20 web app, Electron desktop app (`audio O control`), deployment config, docs.

This report lists what was found, what was changed, how it was verified, and what is
still open. No system is "unhackable"; the goal is a smaller realistic attack surface
with the residual risks written down.

---

## 1. Findings by priority

Status: ✅ fixed in this audit · ⚠️ partially mitigated · 📋 open (recommendation)

### Critical

| # | Finding | Status |
|---|---------|--------|
| C1 | **Real user data committed to git.** `backend/app.db` (5 users: emails, 2 Argon2id hashes) and `backend/test_auth.db` were tracked despite `.gitignore`. | ⚠️ Untracked (`git rm --cached`, local files kept). **Still in git history** — see §4. |
| C2 | **Session forgery if `SESSION_SECRET` is unset in production.** Sessions fell back to a secret that is printed in the public source, so anyone could sign an `aia_session` cookie for any user id, including the admin. | ✅ Production now uses a random per-process secret plus an ERROR log. Dev keeps the old fallback. |

### High

| # | Finding | Status |
|---|---------|--------|
| H1 | **Cross-Site WebSocket Hijacking.** The `/ws/interview` cookie-auth fallback had no `Origin` check. Production cookies are `SameSite=None`, so any website could open an authenticated socket as the visitor and spend their AI quota. | ✅ Cookie auth now requires an origin listed in `CORS_ORIGINS`. Ticket auth is unchanged, because a foreign site can't read a ticket. |
| H2 | **CSRF missing on resource mutations.** Only the auth routes checked CSRF. Resume upload, a multipart request that counts as a "simple" cross-site request, plus resume/role/interview/admin writes had no check. | ✅ All of them now use `require_user_csrf` / `require_csrf`. The SPA interceptor and the Electron header hook already send the header. |
| H3 | **Event-loop blocking.** Argon2 hashing, SMTP sends, PDF/DOCX parsing and synchronous SQLAlchemy calls ran inside `async def` handlers. A burst of logins or a large PDF stalled the same loop that streams live answers. | ✅ DB-only handlers are now plain `def` and run in the threadpool. CPU and DB work inside async handlers goes through `run_in_threadpool`. |
| H4 | **Rate limits could be bypassed.** The limiter keyed on the left-most `X-Forwarded-For` entry, which the client controls, and logins had no per-account limit. | ✅ Logins and forgot-password are now also limited per account. `TRUSTED_PROXY_COUNT` (opt-in) selects the right proxy hop. ⚠️ The default stays legacy so production isn't merged into one bucket. Set it after checking the header shape. |
| H5 | **Password reset didn't end existing sessions.** A stolen cookie stayed valid after the victim reset their password. | ✅ New `users.sessions_revoked_at` column. A reset revokes every older session for REST and WebSocket. |
| H6 | **Unbounded AI spend.** `/interview/analyze` and WS questions had no per-user limit and no input caps. | ✅ `AI_RATE_LIMIT_PER_MINUTE` (default 30) per user. Questions are capped at 4,000 chars and resume/JD at 500k. |
| H7 | **Electron `webSecurity: false`.** Same-origin policy is off in the desktop renderer. | 📋 Not changed: it needs a custom `app://` protocol, a backend CORS change and a packaged-app QA pass (§4). Mitigations added: popups denied, sandbox explicit, IPC sender checks. |
| H8 | **Pre-account-takeover through Google linking.** Signing in with Google links to an existing local account with the same email, even if that account's email was never verified. Someone could register a victim's email first and keep the password. | 📋 Not changed. The app has no email verification, so every local account is "unverified", and a fix would remove password login for legitimate users. Needs a product decision (§4). |
| H9 | **Outdated Electron runtime (33.x, end of life).** The shipped desktop app includes an old Chromium. | 📋 Upgrade to a supported Electron major version (npm audit suggests 44.x) and re-test the packaged app. |

### Medium

| # | Finding | Status |
|---|---------|--------|
| M1 | `/admin/stats` loaded the **whole** `activity_log` table and every user on each request. That table gains two rows per answered question. | ✅ SQL now filters by kind and date window. New composite index `(kind, created_at)`. |
| M2 | Resume list/prune/select queries loaded every stored file (up to 10 MB each). | ✅ `file_data` is now a deferred column and loads only for download or `.txt` edits. |
| M3 | Uploads were read fully into memory before the size check. | ✅ Read in chunks and stopped at max+1 bytes. |
| M4 | **CSV/formula injection** in the admin user export: a signup name like `=HYPERLINK(…)` ran when opened in Excel. | ✅ Cells starting with `= + - @ \t \r` are prefixed with `'`. |
| M5 | Password-reset links, which are live credentials, were written to production logs whenever SMTP failed. | ✅ Logged only outside production. |
| M6 | `/docs`, `/redoc` and `/openapi.json` were public in production. | ✅ Off in production unless `ENABLE_API_DOCS=true`. |
| M7 | `GET /auth/csrf` set the CSRF cookie with a different `SameSite` than login, leaving duplicate cookies in production. | ✅ Shares the same cookie helper. |
| M8 | Initial web bundle was over budget (503.5 kB). Every landing-page visitor downloaded the live-interview pipeline. | ✅ Dashboard and interview are lazy-loaded with selective preloading: **422.3 kB (−16%)**. |
| M9 | Electron IPC handlers didn't check the sender, and there was no window-open handler. | ✅ Each channel checks which window sent it. Popups are denied, and https links open in the system browser. |
| M10 | Rate-limit table grew without bound in memory and had no lock. | ✅ Expired windows are swept and access is locked. |
| M11 | Internal exception text was sent to WebSocket clients. | ✅ Clients get a generic message; the traceback stays in the server log. |
| M12 | `/admin/business` and `/admin/ai-costs` still aggregate full tables in Python. | 📋 Move to SQL `GROUP BY` or daily rollup tables once the data grows. |
| M13 | No migration framework: schema changes are ad-hoc `ALTER`s in `init_db`. | 📋 Adopt Alembic (§4). |
| M14 | **The test suite called live, billed AI providers** using the developer's `backend/.env` keys. It was slow and flaky, and hung when the network stalled. | ✅ `conftest.py` blanks provider keys (opt in with `AIA_TESTS_USE_REAL_KEYS=1`). |
| M15 | Every test run shared `./test_auth.db`, so concurrent or stuck runs deadlocked on the SQLite lock. | ✅ Each process now uses its own temp DB. |

### Low

| # | Finding | Status |
|---|---------|--------|
| L1 | CORS allowed `*` methods and headers. | ✅ Limited to what the SPA sends. |
| L2 | Analyze failures logged the question text (PII). | ✅ Logs the length only. |
| L3 | Dead code: a duplicate unreachable `return` and a leftover debug f-string log. | ✅ Removed. |
| L4 | `__pycache__/*.pyc` files were tracked in git. | ✅ Untracked. |
| L5 | Desktop installer shipped test files and the build-time icon generator. | ✅ Excluded in the `electron-builder` `files` list. |
| L6 | `index.html` JSON-LD claims an `aggregateRating` (4.8 / 128). If those reviews aren't real, this breaks Google's structured-data policy. | 📋 Business decision; left unchanged. |
| L7 | Google JWKS is fetched on every login (no cache). | 📋 Minor; cache the keys for about an hour. |
| L8 | `vercel.json` exists at both the repo root and `frontend/`. | 📋 Keep the one that matches the Vercel project root. |
| L9 | The desktop app has no auto-update channel. | 📋 Add signed updates (`electron-updater` + code signing). |

---

## 2. Batches delivered

### Batch 1 — Security (backend)
**Files:** `app/auth/security.py`, `app/auth/dependencies.py`, `app/auth/service.py`,
`app/api/{auth,resume,roles,interview,admin,websocket}.py`, `app/core/{config,ratelimit}.py`,
`app/main.py`, `app/models/{user,interview}.py`, `app/db/session.py`,
`backend/.env.example`, `tests/test_security_hardening.py` (new), `tests/conftest.py`,
`tests/test_api.py`, `tests/test_auth_protection.py`, `tests/test_llm_model_latency.py`.

**Why it's safe:** the JSON/WebSocket contracts are unchanged, including the strict
`AIInterviewResponse`. The web interceptor and the Electron header hook already sent the
CSRF header, so requiring it everywhere breaks no client. New settings default to the
previous behaviour, except two deliberately safer production defaults: docs hidden, and
no public session secret. The schema change only adds a nullable column.

### Batch 2 — Performance
**Files:** `app/api/admin.py`, `app/models/{activity_log,resume_record}.py`,
`app/db/session.py`, `app/api/resume.py`, `app/services/resume_service.py`,
`frontend/src/app/app.routes.ts`, `frontend/src/app/app.config.ts`,
`frontend/src/app/core/routing/selective-preloading.strategy{,.spec}.ts` (new).

| Metric | Before | After |
|--------|--------|-------|
| Initial JS+CSS (raw) | 503.52 kB (over budget) | **422.29 kB** |
| Initial transfer (est.) | 135.07 kB | **117.47 kB** |
| `/admin/stats` rows read | all `activity_log` rows | only matching kinds in the window (indexed) |
| `GET /resumes` payload read from DB | every stored file blob | metadata only |
| Upload memory for an oversize file | whole body | ≤ limit + 1 byte |

A web-font change was tried and **reverted**: the production build already inlines the
Google Fonts CSS, so it wasn't render-blocking.

### Batch 3 — Desktop hardening
**Files:** `frontend/electron/main.js`, `frontend/package.json` (`build.files` only).
Popups are denied (https links go to the system browser), `sandbox: true` is explicit,
and every IPC channel checks its sender. No desktop UI behaviour changed. The app never
opens popups, which was confirmed with a grep for `window.open` and `target="_blank"`.

### Batch 4 — Docs & repo hygiene
`README.md`, `ARCHITECTURE.md`, `backend/README.md`, `frontend/README.md`,
`frontend/electron/README.md`, this report, `backend/.env.example`; untracked DB files
and `__pycache__`.

---

## 3. Verification

| Check | Result |
|-------|--------|
| Backend tests (baseline at `HEAD`, no keys) | 216 passed, 2 failed (one needed a `.env` file, one depended on a real Groq key) |
| Backend tests (after) | **238 passed, 0 failed** (+20 new security regression tests) |
| Angular unit tests (Karma, ChromeHeadless) | **84 / 84** (81 before + 3 new) |
| Desktop unit tests (`npm run test:electron`) | **16 / 16** |
| `ng build` (production) | ✅, no budget warning |
| `node --check electron/main.js` | ✅ |
| Live smoke test (uvicorn on a throwaway DB) | ✅ 15/15: health, register, login, logout, me, CSRF bootstrap, CSRF enforcement, Google OAuth start (302 to Google), WS ticket + handshake + ping, foreign-origin WS refused, CORS preflight |
| `ruff --select E9,F` | ✅ for changed files (2 pre-existing nits in untouched files) |
| `pip-audit -r requirements.txt` | No known vulnerabilities |
| `npm audit --omit=dev` (shipped web deps) | 0 vulnerabilities |
| `npm audit` (dev/build tooling) | 28 → 25 after a non-breaking `npm audit fix`. The rest need major upgrades (`electron`, `electron-builder`, `karma`) |

Not run: the Electron packaging build and `.exe` installer (`npm run dist:win`), per the
repo's agent policy (`AGENTS.md`). The desktop changes were checked with the desktop unit
tests and a syntax check only. **Run a packaged-app QA pass before releasing.**

---

## 4. Residual risks and next steps (in priority order)

1. **Purge the committed databases from git history** (C1). Run
   `git filter-repo --path backend/app.db --path backend/test_auth.db --invert-paths`,
   force-push, and have collaborators re-clone. Treat the five accounts as exposed: ask
   the two password users to rotate their passwords. *This rewrites history, so it
   needs your approval.*
2. **Confirm `SESSION_SECRET` is set on Render** (C2). If it was ever unset, rotate it
   (this signs everyone out).
3. **Set `TRUSTED_PROXY_COUNT`** (H4). Check the `X-Forwarded-For` shape in the Render
   request log; Vercel → Render is normally `2`. Until then, the per-account limits are
   the effective brute-force protection.
4. **Electron security** (H7, H9). Upgrade Electron to a supported major version, then
   replace `file://` + `webSecurity:false` with a privileged `app://` scheme
   (`protocol.handle`), add `app://.` (or the chosen origin) to `CORS_ORIGINS`, and
   re-enable `webSecurity`. Add code signing and `electron-updater` (L9).
5. **Email verification**, then **verified-only Google linking** (H8). Once emails are
   verified, linking Google to an unverified local account should clear the password
   and revoke its sessions.
6. **Alembic migrations** (M13). Generate an initial revision from the current models
   and move the `_ensure_*` helpers into versioned migrations with downgrade steps.
7. **Admin analytics at scale** (M12). Use SQL aggregation or daily rollups for
   `/admin/business` and `/admin/ai-costs`.
8. **Multi-worker readiness.** The rate limiter and per-user interview context live in
   process memory. Before running more than one worker or instance, move them to Redis
   (or keep sticky sessions plus one worker).
9. **Index cleanup.** `ix_activity_log_kind` is now a left prefix of the new composite
   index. After confirming with `EXPLAIN` that no plan still prefers it, drop it
   (`DROP INDEX ix_activity_log_kind`) to save write overhead.

---

## 5. Schema change record

| Change | Impact | Migration | Rollback |
|--------|--------|-----------|----------|
| `users.sessions_revoked_at TIMESTAMPTZ NULL` | Additive and nullable, no backfill, no lock beyond a metadata `ALTER`. Older code ignores it. | Automatic at startup (`_ensure_user_columns`, idempotent). | `ALTER TABLE users DROP COLUMN sessions_revoked_at;` |
| `CREATE INDEX ix_activity_log_kind_created_at ON activity_log (kind, created_at)` | Faster admin range scans; slightly more write cost per activity row. On PostgreSQL the build blocks writes to `activity_log` while it runs (small table today). | Automatic at startup (`_ensure_activity_indexes`, `IF NOT EXISTS`). For a large table, pre-create it with `CREATE INDEX CONCURRENTLY` before deploying. | `DROP INDEX IF EXISTS ix_activity_log_kind_created_at;` |
