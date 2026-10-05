import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { catchError, throwError } from 'rxjs';

/**
 * Logs every failed HTTP request (4xx / 5xx / network errors) with enough
 * context to correlate against the backend's request-id logs.
 *
 * The backend logs one line per request with a request id, so a client-side
 * `[http] 502 POST /api/interview/analyze` here matches the server's
 * `[<id>] <-- POST /api/interview/analyze -> 502` line — that pairing is what
 * turns an opaque error into a diagnosable one.
 */
export const errorLoggingInterceptor: HttpInterceptorFn = (req, next) => {
  return next(req).pipe(
    catchError((err: unknown) => {
      const method = req.method.toUpperCase();
      const url = req.urlWithParams;

      if (err instanceof HttpErrorResponse) {
        const { status, statusText, message } = err;
        // Skip noisy logging for expected 401s — auth.interceptor already
        // handles those by redirecting to /login.
        if (status === 0) {
          // Network-level failure: backend unreachable, DNS, CORS preflight.
          console.error(`[http] NETWORK_ERROR ${method} ${url} — backend unreachable? (${message})`);
        } else {
          console.error(`[http] ${status} ${method} ${url} — ${statusText || message}`);
        }
      } else {
        console.error(`[http] ${method} ${url} — ${String(err)}`);
      }

      return throwError(() => err);
    }),
  );
};
