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
  /**
   * Backend origin for the live-interview WebSocket.
   * Empty = same-origin `/ws/interview`, which the Angular dev-server proxy
   * forwards to http://localhost:8000 (see proxy.conf.json).
   */
  wsBaseUrl: '',
};
