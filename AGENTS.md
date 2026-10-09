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
