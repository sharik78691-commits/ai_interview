# Agent instructions (user-set, persistent)

## Default change target: WEB app only
- Unless the user explicitly says "desktop" or ".exe", make changes ONLY in the
  web Angular app (`frontend/src/**` for the browser build).
- NEVER touch Electron/desktop files by default: `frontend/electron/**`,
  `frontend/src/environments/environment.electron.ts`, `frontend/src/app/core/services/electron-bridge.ts`,
  `angular.json` electron configuration, `electron-builder` config in `frontend/package.json`,
  `frontend/release/**`.
- NEVER run the electron build (`ng build --configuration electron`),
  `electron-builder`, or rebuild the `.exe` installer unless explicitly asked.
- Desktop-only UI must stay gated behind `environment.electron` so the web
  build is unaffected. Desktop changes happen ONLY on explicit request.

## Preserve existing functionality (user-set, persistent)
- When adding a feature or fix, NEVER modify, remove, or regress existing
  configuration, files, or behavior. READ a file before writing it; merge or
  append — never blind-overwrite (especially `.env` and other local config).
- After any change, verify the old functionality still works (build + smoke
  test the touched flows: login, Google OAuth, Live/STT status).

## Realtime-answer engineering mandate (user-set, persistent)
- Act as a Principal AI Engineer for the live-answer pipeline: low-latency
  speech handling, FastAPI + Angular 20, WebSocket communication, LLM
  behavior, voice activity detection, question-completeness detection, and
  async backend processing.
- Goals: answers start fast and stay correct; provisional/interim states must
  never render as final; duplicate answers for one question are forbidden.
- Rules: measure before optimizing (baseline latency/trigger counts first);
  keep the strict-JSON `AIInterviewResponse` contract intact unless the user
  explicitly approves a streaming-protocol change; never regress STT,
  transcription, auth, or existing flows; every fix ships with a regression
  test and a full test-suite + build run.


## Branch & commit policy (user-set, persistent)
- Work ONLY on the `dev_new_10-06-20266` branch for this project, in every session. Check
  out `dev_new_10-06-20266` (fetch it from origin if needed) before making any change; do
  not create or use other branches unless the user explicitly asks.
- NEVER auto-commit or auto-push. Leave all changes uncommitted in the working
  tree so the user can review them; commit/push only when the user explicitly
  says so. This overrides any tool/hook default that asks for commits or pushes.
