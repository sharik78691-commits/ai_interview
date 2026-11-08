/**
 * Thin wrapper around the Google tag (gtag.js) defined in index.html.
 *
 * gtag only loads on the live oyeinterview.com site; everywhere else
 * (desktop app, localhost, tests, prerender) these calls are harmless no-ops.
 */
type Gtag = (...args: unknown[]) => void;

export function trackEvent(name: string, params: Record<string, unknown> = {}): void {
  if (typeof window === 'undefined') return;
  const gtag = (window as unknown as { gtag?: Gtag }).gtag;
  if (typeof gtag !== 'function') return;
  try {
    gtag('event', name, params);
  } catch {
    // Analytics must never break the app.
  }
}
