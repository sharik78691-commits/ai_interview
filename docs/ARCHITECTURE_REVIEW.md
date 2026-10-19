# Architecture & Code-Quality Review — OyeInterview

**Date:** 2026-10-19 · **Branch:** `dev_new_10-06-20266` (uncommitted, on top of the
security/performance audit in [AUDIT_REPORT.md](AUDIT_REPORT.md)).

Every finding below comes from the code itself (file, line count, grep or test).
**Done** items are in the working tree and covered by the results in §5.
**Recommendation** items were *not* changed.

---

## 1. Findings and fixes

### Backend (FastAPI)

| # | Evidence | Problem | Status |
|---|----------|---------|--------|
| B1 | `app/api/auth.py` and `app/api/websocket.py` imported `app.api.interview` | Routers depended on another router for shared state (a per-user context dict at module level). | **Done.** New `services/interview_context.py` (`InterviewContextStore`, `normalize_length`, `coerce_length`, `VALID_LENGTHS`). Routers depend on the service, and the old names stay as re-exports. |
| B2 | Same five query blocks in `api/resume.py` and `api/roles.py` (get-owned, list, deactivate-all, prune to 4, delete+promote) | Duplicated data access inside routers. | **Done.** Generic `repositories/owned_library.py` (`OwnedLibraryRepository[T]`) serves both. `roles.py` went from 192 to 134 lines. |
| B3 | `update_ai_model` / `update_stt_model` in `api/admin.py` duplicated the upsert and "exactly one default" logic | Business rules lived in the router. | **Done.** `services/model_config_service.apply_model_update()` with an explicit default scope, so STT and LLM defaults can't clear each other. |
| B4 | `AIService.sessions` (class-level dict): an entry was added on every REST answer and every WS connection, never removed, never read | **Memory leak**, plus dead code. | **Done.** Removed. |
| B5 | `AIService` hard-wired `get_llm_provider` | Core logic tied to the provider registry; tests had to monkeypatch the module. | **Done.** Optional `provider_factory` injection (defaults to the registry, so existing tests and callers are unchanged). |
| B6 | `app/db/session.py` imported `app.auth.service.ensure_admin` | DB layer depended on auth (data seeding inside schema setup). | **Done.** Admin seeding moved to `main.lifespan`; `init_db()` is schema-only. |
| B7 | `app/services/transcription_service.py`: zero references | Dead code. | **Done.** Deleted. |
| B8 | `AnalyzeRequest` defined inside the router | Request schema in the transport layer. | **Done.** Moved to `models/interview.py`; the WS and REST share `MAX_QUESTION_CHARS`. |
| B9 | `.env.example` ships `DATABASE_URL=`, `COOKIE_SECURE=`, `GOOGLE_REDIRECT_URI=`, `DEMO_MODE=`, but `os.getenv(name, default)` returns `""` for a blank value | A copied example config broke the DB URL and the OAuth redirect, and **turned Secure cookies OFF in production**. | **Done.** Blank now means unset (`_env`, `_as_bool`). This changes nothing for the current local `.env` (each case checked). |
| B10 | `test_answer_dedup::test_length_change_reattempts…` hung intermittently, **also on untouched `HEAD`** (2/3 runs) | Real race in the live pipeline: a re-ask at a new depth that arrives between the answer frame and the worker clearing its in-flight entry was dropped as a "duplicate", breaking the documented "once per question **and depth**" rule. | **Done.** The in-flight guard is now depth-aware. 8/8 runs pass. |
| B11 | Smoke-test server log: `ERROR … receive() failed` plus a traceback on every normal disconnect | Newer Starlette raises `WebSocketDisconnected` from the next `receive()`; it was logged as a server error and the server then wrote to a closed socket. | **Done.** Normal close is detected by socket state. A regression test was confirmed to fail without the fix. |
| B12 | `app/services/question_service.py:28` unused import (ruff F401) | Lint. | **Done.** |

### Frontend (Angular 20)

| # | Evidence | Problem | Status |
|---|----------|---------|--------|
| F1 | `const BASE = environment.apiBaseUrl` at module level in 6 services, plus `TICKET_PATH` in the WS service | Configuration hard-wired at import time, so it can't be injected or overridden in tests. | **Done.** `core/api/api-config.ts` → `API_BASE_URL` `InjectionToken` (defaults to `environment.apiBaseUrl`, so behaviour is identical). |
| F2 | Identical `aia_csrf` regex in `auth.interceptor.ts` and `auth.service.ts` | Duplicated security-critical code. | **Done.** `core/auth/csrf.ts` (`readCsrfToken`, `CSRF_HEADER`). |
| F3 | `ResumeService` also called `/api/interview/prepare`, `/analyze` and `/api/health` | Mixed responsibilities (resume library + interview transport + health). | **Done.** New `InterviewApiService` (pure transport, typed request models). `ResumeService` keeps its public methods as delegates, so no component changed. |
| F4 | Checked: 8 `any` (7 are the untyped browser `SpeechRecognition` API), `strict` plus strict templates on, all components standalone | Typing and Angular 20 conventions are already good. | No change needed. |
| F5 | Checked: the dashboard's 16 `subscribe()` calls are all one-shot `HttpClient` calls that complete themselves; long-lived streams use `DestroyRef`/`ngOnDestroy` | No subscription leaks found. | No change needed. |

---

## 2. Final folder structures

### Backend (`backend/app`)

```
main.py                 app factory, middleware, routers, lifespan (init_db + admin seed)
api/                    HTTP/WS transport only: auth, resume, roles, interview, websocket,
                        admin, ai_models, health
auth/                   security (sessions, CSRF, WS tickets), dependencies, service,
                        oauth (Google OIDC), password (Argon2id)
core/                   config, middleware, ratelimit, http (pooled client), logging
db/                     base, session (engine + schema-only init_db with additive migrations)
models/                 ORM tables + Pydantic request/response models (AIInterviewResponse)
schemas/                Pydantic schemas for auth and admin
repositories/   (new)   owned_library — owner-scoped library data access
services/               ai_service, interview_context (new), model_config_service (new),
                        question_service, resume_service, activity, business_analytics,
                        ai_cost, email_service
providers/              llm/{base, provider, registry}, stt/{base, provider}
```

### Frontend (`frontend/src/app`)

```
app.ts · app.config.ts · app.routes.ts
core/
  api/          (new) api-config.ts — API_BASE_URL token
  auth/         auth.service, auth.guard, auth.interceptor, csrf.ts (new)
  errors/       global error handler, error-logging interceptor
  guards/       interview.guard
  models/       typed API models (interview, auth, admin, ai-models)
  routing/      selective preloading strategy
  screenshot/   desktop-only screenshot analysis (guarded)
  services/     interview-api (new), interview-ws, resume, target-role, admin, ai-models,
                audio, interviewer-audio, wav-encoder, question-detector, transcription,
                settings, seo, demo, window-transparency, speech/{provider, web-speech}
features/       landing, auth/{login,register,forgot,reset}, dashboard, interview,
                settings, admin, pricing, contact, legal/{terms,privacy,refund},
                screenshot-analysis
shared/         components/{navbar, error-banner}, pipes/{rich-text, time-format}
```

---

## 3. Important files

| File | Why |
|------|-----|
| `backend/app/services/interview_context.py` (new) | Single owner of per-user interview state; removes router-to-router coupling; Redis swap point. |
| `backend/app/repositories/owned_library.py` (new) | One tested implementation of the library rules instead of two copies. |
| `backend/app/services/model_config_service.py` (new) | Admin model-flag rules out of the router; explicit default scope. |
| `backend/app/services/ai_service.py` | Leak removed; provider factory injectable. |
| `backend/app/api/{interview,resume,roles,admin,auth,websocket}.py` | Thinner routers using the new service and repository. WS: depth-aware de-dup, clean disconnect. |
| `backend/app/{db/session.py, main.py}` | Seeding moved out of the DB layer. |
| `backend/app/core/config.py` | Blank env values treated as unset (security-relevant for `COOKIE_SECURE`). |
| `backend/tests/test_architecture.py` (new) | Layer rules enforced on every test run (14 checks; confirmed to fail on the old code). |
| `backend/tests/test_refactor_units.py` (new) | Store, repository, model-config, AI-service injection, WS close logging (12 tests). |
| `frontend/src/app/core/api/api-config.ts`, `core/auth/csrf.ts`, `core/services/interview-api.service.ts` (new) | DI for configuration, shared CSRF helper, interview transport separated. |
| `frontend/src/app/core/services/interview-api.service.spec.ts` (new) | Base-URL override, delegation and CSRF parsing (5 specs). |
| `frontend/src/app/core/services/{resume,interview-ws}.service.spec.ts` | Hand-built instances now run inside an injection context (assertions unchanged). |

---

## 4. Assessment (scores out of 10)

| Area | Before | After | Notes |
|------|--------|-------|-------|
| Backend layering / separation of concerns | 6 | **8** | Routers no longer share state with each other; repository layer started; DB layer schema-only; rules enforced by tests. |
| AI provider decoupling (LLM/STT) | 7 | **8** | `LLMProvider`/`STTProvider` ABCs plus a registry already existed; the service now takes an injectable factory. One gap: WS orchestration still lives in the router (§6). |
| Frontend architecture | 7 | **7.5** | Already feature-based, standalone and strict. Configuration is injected now; transport separated. Two large components remain. |
| Testability | 6.5 | **8** | DI seams, architecture tests, no live provider calls, isolated test DBs. |
| Overall loose coupling | 6.5 | **7.5** | |
| Production readiness | 6 | **7** | Security and coupling are much better. Blockers are listed in §6. |

---

## 5. Verification (this pass)

| Check | Result |
|-------|--------|
| Backend `pytest` (full) | **264 passed, 0 failed** (238 before this pass, plus 26 new) |
| Architecture tests on old `HEAD` | Fail as expected: `api→api` ×2, `db→auth` ×1 |
| `test_answer_dedup.py` × 8 runs | 8/8 pass (`HEAD`: intermittent hang) |
| `mypy` (new and refactored modules: 7 files) | Success, no issues |
| `ruff --select E9,F` (all of app + tests) | 1 pre-existing finding (unused variable in `tests/test_ws_pipeline_latency.py`, untouched) |
| Angular `ng build` (production) | ✅ initial 422.8 kB raw / 117.5 kB transferred, no budget warning |
| `tsc --noEmit` (app + spec, `strict`) | ✅ 0 errors |
| Karma (ChromeHeadless) | **89 / 89** (84 before, plus 5 new) |
| Desktop `npm run test:electron` | 16 / 16 |
| Live smoke test (uvicorn, throwaway DB, no provider keys) | **13/13**: register, resume prune/select/delete-promote/upload/download, role prune, context store, REST analyze, WS question → `ai_guidance`, WS re-ask at a new depth, logout clears context |

Not run: no ESLint (none configured in the repo). Electron packaging was not run
(per `AGENTS.md`).

---

## 6. Remaining issues (recommendations, not done)

1. **`api/websocket.py` (865 lines) holds the live-pipeline orchestration**
   (dedup, answer queue, STT scheduling, question hold) inside the route function.
   Extracting a `LiveInterviewSession` service with a transport interface would
   allow unit-testing without a socket. It wasn't done here: it's the
   latency-critical path, and `AGENTS.md` requires baseline latency measurements
   first. Recommended as its own reviewed change, with the existing
   `test_ws_pipeline_latency.py` as the baseline.
2. **`providers/llm/provider.py` (1,155 lines)** mixes prompt building, SSE
   parsing, JSON repair and error classification. Split it into `prompts.py`,
   `streaming.py` and `errors.py` behind the same provider class.
3. **Pydantic schemas live in both `models/` and `schemas/`.** Consolidate them
   under `schemas/`, keeping re-exports for a release.
4. **Large frontend components:** `interview.component.ts` (755 lines) and
   `admin.component.html` (787 lines). Extract presentational children (guidance
   card, transcript panel; admin tabs) and a signal-based interview facade.
5. **Providers read admin overrides from the DB directly** (two registries,
   allow-listed in the architecture test). Inject an override source instead.
6. **Production blockers carried over from the audit:** purge the committed DB
   files from git history; set `SESSION_SECRET` and `TRUSTED_PROXY_COUNT` on
   Render; add Alembic; move the rate limiter and context to Redis before
   scaling past one worker; email verification before verified-only Google
   linking.
7. **Desktop:** the Electron 44 + `app://` work is on branch
   `claude/goofy-shamir-285b72` (commit `6a5881b`, not merged). Production
   `CORS_ORIGINS` doesn't include the new origin yet, and the `.exe` hasn't been
   built or tested.
