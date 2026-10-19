import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { retry, throwError, timer } from 'rxjs';

/** Never make the user wait longer than this for an automatic retry. */
export const MAX_RATE_LIMIT_WAIT_MS = 10_000;
/** Wait used when a 429 carries no (valid) Retry-After header. */
export const DEFAULT_RATE_LIMIT_WAIT_MS = 1_000;

/** Milliseconds to wait for a 429, from its Retry-After seconds; null = too long to wait. */
export function rateLimitWaitMs(err: HttpErrorResponse): number | null {
  const seconds = Number(err.headers?.get('Retry-After'));
  const ms = Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : DEFAULT_RATE_LIMIT_WAIT_MS;
  return ms <= MAX_RATE_LIMIT_WAIT_MS ? ms : null;
}

/**
 * A GET answered 429 (rate limited) is retried ONCE after the server's
 * Retry-After, instead of failing the screen immediately. GETs are
 * idempotent, so a retry can never duplicate an action; POST/PATCH/DELETE are
 * never retried. Longer waits are not hidden from the user — the 429 is
 * surfaced so the caller can show a clear message.
 */
export const rateLimitRetryInterceptor: HttpInterceptorFn = (req, next) => {
  if (req.method.toUpperCase() !== 'GET') return next(req);
  return next(req).pipe(
    retry({
      count: 1,
      delay: (err: unknown) => {
        if (err instanceof HttpErrorResponse && err.status === 429) {
          const wait = rateLimitWaitMs(err);
          if (wait !== null) return timer(wait);
        }
        return throwError(() => err);
      },
    }),
  );
};
