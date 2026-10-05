import { ErrorHandler, Injectable } from '@angular/core';

/**
 * Global fallback for any uncaught Angular error (template errors, unhandled
 * promise rejections routed through NgZone, runtime exceptions in event
 * handlers). Without this, an error in a less-travelled code path can vanish
 * silently and leave the UI in a broken state with no record of why.
 *
 * Logs to the console; a production deployment can extend this to POST to a
 * /api/logs sink or a monitoring service (Sentry, etc.) without changing any
 * call site.
 */
@Injectable()
export class GlobalErrorHandler implements ErrorHandler {
  handleError(error: unknown): void {
    // Log the full object so stack + context survive; the console already
    // expands Error instances, but this guards against string/object payloads.
    if (error instanceof Error) {
      console.error('[app] uncaught error:', error.message, '\n', error.stack ?? '(no stack)');
    } else {
      console.error('[app] uncaught error:', error);
    }

    // Keep the default behavior for anything Angular itself relies on.
    // No rethrow: an uncaught error should not take down the whole app.
  }
}
