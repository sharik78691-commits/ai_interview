# Frontend — Angular 20 web app (and desktop renderer)

The oyeinterview.com single-page app. It covers the landing and pricing pages,
authentication, the dashboard (resume and target-role libraries), the live
interview screen (mic and meeting-audio transcription, streamed AI guidance),
settings, and the admin panel. The same codebase is the renderer for the Electron
desktop app; see [electron/README.md](electron/README.md).

**Stack:** Angular 20 (standalone components, signals), TypeScript 5.9, RxJS,
hand-written CSS, Karma + Jasmine.

System design: [../ARCHITECTURE.md](../ARCHITECTURE.md)

---

## Local development

```powershell
cd frontend
npm ci
npm start            # ng serve → http://localhost:4200
```

`proxy.conf.json` forwards `/api` and `/ws` to `http://localhost:8000`, so run the
backend too ([../backend/README.md](../backend/README.md)). Cookies then stay
same-origin, exactly as in production.

## Environments

| File | Used by | Key values |
|------|---------|------------|
| `src/environments/environment.ts` | `ng serve`, development build | `apiBaseUrl: ''`, `wsBaseUrl: ''` (same origin via the proxy) |
| `src/environments/environment.prod.ts` | `ng build` (default, Vercel) | `apiBaseUrl: ''` (Vercel rewrites `/api`), `wsBaseUrl: 'wss://<render-host>'` |
| `src/environments/environment.electron.ts` | `ng build --configuration electron` | absolute API/WS URLs, `electron: true` (desktop-only UI) |

No secrets ever go in these files. Provider keys stay on the backend.

## Scripts

| Command | What it does |
|---------|--------------|
| `npm start` | Dev server with the API proxy |
| `npm run build` | Production web build → `dist/frontend/browser` |
| `npm test` | Karma unit tests (`npx ng test --watch=false --browsers=ChromeHeadless` for CI) |
| `npm run test:electron` | Node tests for the desktop main-process modules |
| `npm run build:electron` / `dist:win` | Desktop build: see [electron/README.md](electron/README.md) |

## Structure

```
src/app/
  app.routes.ts          route map (lazy routes, guards, per-route SEO data)
  app.config.ts          router (selective preloading, hash routing on desktop), HTTP interceptors
  core/
    auth/                AuthService (session state), guards, authInterceptor (credentials + CSRF)
    errors/              global error handler, HTTP error-logging interceptor
    routing/             SelectivePreloadingStrategy
    services/            interview WebSocket, audio capture, interviewer audio → WAV,
                         question detector, transcription, resume/role/admin/settings APIs
    screenshot/          desktop-only screenshot analysis (gated by desktopOnlyGuard)
  features/              landing, auth/*, dashboard, interview, settings, admin, legal, pricing, contact
  shared/                navbar, error banner, pipes
```

## Performance

* **Initial bundle:** only the landing and auth pages load eagerly (≈422 kB raw /
  ≈117 kB transferred). Dashboard and interview are lazy chunks flagged
  `data: { preload: true }`. `SelectivePreloadingStrategy` downloads them right
  after first paint, so the signed-in flow is still instant. Admin, legal, pricing
  and settings load only on navigation.
* **Budgets** (`angular.json`): warning at 500 kB initial, error at 1 MB. Keep new
  heavy features lazy.
* **Fonts:** the production build inlines the Google Fonts CSS
  (`optimization.fonts`), so the font isn't render-blocking. Don't swap the
  `<link>` for a `media="print"` trick; that disables inlining.
* `provideZoneChangeDetection({ eventCoalescing: true })` cuts change-detection
  passes during high-frequency transcript events.

## Security

* Auth is an **HttpOnly session cookie** set by the backend. No tokens are kept in
  `localStorage`/`sessionStorage`.
* `authInterceptor` sends `withCredentials` on `/api` calls and echoes the readable
  `aia_csrf` cookie as `X-CSRF-Token` on every POST/PUT/PATCH/DELETE. The backend
  rejects mutations without it.
* A 401 on a protected call clears local auth state and redirects to `/login`.
* Rich text is rendered through `RichTextPipe`; don't bind raw HTML with
  `[innerHTML]` without sanitisation.
* Desktop-only UI must stay behind `environment.electron` so the web build is
  unaffected.

## Deployment (Vercel)

`vercel.json`: `npm ci` → `npm run build` → serve `dist/frontend/browser`, with
rewrites `/api/*` and `/ws/*` → Render backend and `/*` → `index.html`. After
changing the backend host, update both `vercel.json` and `wsBaseUrl` in
`environment.prod.ts`.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Logged out on production only | Check that `/api/auth/me` reaches the backend through the Vercel rewrite (same-origin) and that `COOKIE_DOMAIN` is empty. |
| Live answers never arrive | Open devtools → Network → WS: is the ticket request 200 and the socket open? A 1008 close means auth failed (see the backend troubleshooting table). |
| Microphone / tab audio blocked | Browser permission prompt, `Permissions-Policy`, and HTTPS (required outside localhost). |
| `bundle initial exceeded maximum budget` | A heavy import landed in an eager route. Move it behind `loadComponent`. |
