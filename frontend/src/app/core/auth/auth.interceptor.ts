import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { AuthService } from './auth.service';

/**
 * Ensures API requests carry the secure session cookie and the CSRF header.
 *
 * - `withCredentials` sends the HttpOnly session cookie (same-origin in dev via
 *   the proxy, and in production behind the same domain).
 * - The readable `aia_csrf` cookie is echoed in `X-CSRF-Token` for unsafe
 *   methods (double-submit CSRF protection).
 * - A 401 on a protected call clears local auth state and redirects to /login.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  const isApi = req.url.includes('/api/');
  const unsafe = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(req.method.toUpperCase());

  let headers = req.headers;
  if (isApi && unsafe) {
    const match = document.cookie.match(/(?:^|;\s*)aia_csrf=([^;]+)/);
    const token = match ? decodeURIComponent(match[1]) : '';
    if (token) headers = headers.set('X-CSRF-Token', token);
  }

  const cloned = req.clone({
    withCredentials: isApi,
    headers,
  });

  return next(cloned).pipe(
    catchError((err: HttpErrorResponse) => {
      // Session expired / invalid on a protected endpoint.
      if (err.status === 401 && isApi && !req.url.includes('/api/auth/')) {
        auth.checkSession().subscribe();
        router.navigate(['/login'], { queryParams: { expired: '1' } });
      }
      return throwError(() => err);
    }),
  );
};
