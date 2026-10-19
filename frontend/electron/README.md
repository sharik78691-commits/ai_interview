# Desktop app — "audio O control" (Electron)

A Windows/macOS tray app that wraps the Angular renderer and adds:

* **Invisible mode:** the main window and the floating Copilot window use
  `setContentProtection(true)`, so Zoom, Meet, Teams and OBS capture them as black.
* **Tray-only presence:** no taskbar button. `Ctrl+Shift+A` toggles the window.
* **Transparency slider** (`window:set-opacity`).
* **Screenshot analysis:** capture a screen or window, then send it to a vision
  model from the **main process**, using a key stored with OS encryption.

Architecture overview: [../../ARCHITECTURE.md §5](../../ARCHITECTURE.md#5-desktop-application-frontendelectron)

## Files

| File | Role |
|------|------|
| `main.js` | App lifecycle, windows, tray, hotkey, IPC handlers, permission handler, CSRF header hook |
| `preload.js` | `window.electronAPI` bridge for the main window (invisible mode, opacity, screenshot) |
| `copilot.html` + `copilot-preload.js` | Always-on-top Copilot window (`window.copilotAPI`) |
| `screenshot/capture.js` | `desktopCapturer` sources and capture |
| `screenshot/analysis.js`, `model-config.js`, `image-validation.js` | Backend request (`POST /api/screenshots/analyze` with the user's session), upload limits, input validation |
| `reload-recovery.js` | Reloads `index.html` on the same route when a reload hits the bare app folder (was a blank window) |
| `screenshot/ipc.js` | IPC surface; rejects any sender other than the main window |
| `make-tray-icon.js` | Build-time generator for `tray-icon.ico` (not shipped) |

## Develop

```powershell
cd frontend
npm ci
npm start                 # terminal 1: Angular dev server on :4200
npm run electron          # terminal 2: Electron loading http://localhost:4200
npm run test:electron     # unit tests for screenshot/* modules
```

## Build the installer

```powershell
npm run dist:win          # ng build --configuration electron && electron-builder --win  → release/
npm run dist:mac          # on macOS
```

* `environment.electron.ts` sets absolute URLs: REST through
  `https://www.oyeinterview.com` (so the session cookie matches the site) and the
  WebSocket straight to the Render host.
* Packaged files (`package.json` → `build.files`): `electron/**` (minus tests and
  the icon generator), `dist/frontend/browser/**`, `package.json`.
* Output goes to `frontend/release/` (gitignored). Don't commit installers.

## Security model

| Control | Status |
|---------|--------|
| `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true` | ✅ both windows |
| Narrow preload bridges (no generic `ipcRenderer` exposure) | ✅ |
| IPC sender validation (main window vs Copilot) on every channel | ✅ |
| Popups denied; https links open in the system browser | ✅ |
| Permissions: only media/audio/video capture granted | ✅ |
| No AI key on the client: screenshot analysis runs on the backend with the server's `GEMINI_API_KEY` | ✅ |
| Navigation to the public website bounced back to the packaged UI | ✅ |
| `webSecurity: false` (needed for `file://` + cross-origin API) | ⚠️ known gap. Plan: serve the UI from a privileged `app://` scheme via `protocol.handle`, add that origin to backend `CORS_ORIGINS`, re-enable `webSecurity` |
| Electron 33 (end of life) | ⚠️ upgrade to a supported major version (security patches for Chromium) |
| Code signing + signed auto-update (`electron-updater`) | 📋 not set up yet: unsigned builds trigger SmartScreen and can't be updated safely |

## Performance notes

* `disableHardwareAcceleration()` is deliberate: content-protected windows can
  render black with GPU compositing on Windows. The UI is mostly text, so the cost
  is small.
* `backgroundThrottling: false` keeps the WebSocket and guidance pipeline live
  while the window is hidden in the tray.
* The HTTP cache is cleared on startup so a reinstalled build never shows a stale
  bundle. That costs one cold load of the local files, which is acceptable for a
  `file://` app.
* The renderer is the same lazy-loaded Angular build as the web app.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Window is black on my own screen | Expected with GPU compositing. Hardware acceleration is disabled for this reason; update GPU drivers if it persists. |
| Login works but actions fail with 403 | The CSRF header hook needs the `aia_csrf` cookie for `*.oyeinterview.com`. Sign out and back in. |
| Hotkey doesn't work | Another app owns `Ctrl+Shift+A` (a warning is logged at startup). |
| Screenshot analysis "not set up on the server" | Set `GEMINI_API_KEY` (and optionally `SCREENSHOT_MODEL`) in the backend's environment and redeploy. Nothing is configured on the user's PC. |
