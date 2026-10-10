/**
 * Production environment.
 *
 * Used by `ng build` (production is the default build configuration).
 * Vercel runs `npm run build` → `ng build` → this file is used automatically.
 */
export const environment = {
  production: true,
  electron: false,
  /**
   * Base URL for REST calls (/api/*). Empty = same-origin: Vercel proxies /api
   * to the backend, so the session cookie stays same-origin.
   */
  apiBaseUrl: '',
  /**
   * Backend origin for the live-interview WebSocket.
   * Static hosts (Vercel) cannot reliably proxy a WebSocket upgrade to an
   * external origin, so the socket connects DIRECTLY to the backend and
   * authenticates with a short-lived ticket (see /api/auth/ws-ticket).
   */
  wsBaseUrl: 'wss://ai-interview-1-309j.onrender.com',
  /**
   * Desktop-app installers offered on the dashboard download card.
   * Paste the hosted file links when a build is published (GitHub Release,
   * Drive, or CDN). Empty = button shows "coming soon" instead of a dead link.
   */
  desktopApp: {
    version: '1.0.0',
    windowsUrl: '',
    macUrl: '',
  },
};
