/** Name of the readable double-submit cookie issued by the backend. */
export const CSRF_COOKIE = 'aia_csrf';
/** Header the backend compares against the cookie on POST/PUT/PATCH/DELETE. */
export const CSRF_HEADER = 'X-CSRF-Token';

const CSRF_COOKIE_RE = new RegExp(`(?:^|;\\s*)${CSRF_COOKIE}=([^;]+)`);

/** Current CSRF token from `document.cookie` ('' when absent). */
export function readCsrfToken(cookies: string = typeof document === 'undefined' ? '' : document.cookie): string {
  const match = cookies.match(CSRF_COOKIE_RE);
  return match ? decodeURIComponent(match[1]) : '';
}
