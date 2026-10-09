/**
 * Electron (desktop) environment.
 *
 * Used by `ng build --configuration electron` (see angular.json fileReplacements).
 *
 * A packaged app is loaded from `file://`, so there is NO dev-server proxy and
 * NO same-origin backend — every call must use absolute URLs to the deployed
 * backend. Point these at wherever the backend is hosted.
 */
export const environment = {
  production: true,
  /**
   * Desktop build. Hash routing is required because the app is loaded from
   * file:// — path URLs like /interview do not exist as files.
   */
  electron: true,
  /**
   * The session cookie is issued for www.oyeinterview.com (same-origin proxy),
   * not the Render host. Calling Render directly from the desktop app drops the
   * login, and Google OAuth then dumps the window onto the old website.
   */
  apiBaseUrl: 'https://www.oyeinterview.com',
  /** Absolute WebSocket origin for the live-interview socket. */
  wsBaseUrl: 'wss://ai-interview-1-309j.onrender.com',
};
