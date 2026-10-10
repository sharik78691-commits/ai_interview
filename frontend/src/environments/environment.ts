/**
 * Development (local) environment.
 *
 * Used by `ng serve` / `ng build --configuration development`.
 * Angular's `fileReplacements` (in angular.json) swaps this file with
 * `environment.prod.ts` when building for production — so the SAME committed
 * code runs locally and in production with no manual edits.
 */
export const environment = {
  production: false,
  electron: false,
  /**
   * Base URL for REST calls (/api/*). Empty = same-origin, which the Angular
   * dev-server proxy forwards to http://localhost:8000 (see proxy.conf.json).
   */
  apiBaseUrl: '',
  /**
   * Backend origin for the live-interview WebSocket.
   * Empty = same-origin `/ws/interview`, which the Angular dev-server proxy
   * forwards to http://localhost:8000 (see proxy.conf.json).
   */
  wsBaseUrl: '',
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
