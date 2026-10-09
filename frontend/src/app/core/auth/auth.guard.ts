import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { map } from 'rxjs';
import { AuthService } from './auth.service';
import { environment } from '../../../environments/environment';

/**
 * Protects authenticated routes (/dashboard, /interview, /settings).
 *
 * The guard is a UX convenience only — the backend enforces authorization on
 * every protected endpoint. Unauthenticated users are redirected to /login.
 */
export const authGuard: CanActivateFn = (_route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  if (auth.isAuthenticated) return true;

  // Verify with the server (cookie may still be valid after a reload).
  return auth.checkSession().pipe(
    map((user) => {
      if (user) return true;
      return router.createUrlTree(['/login'], {
        queryParams: { redirect: state.url },
      });
    }),
  );
};

/** Redirects already-authenticated users away from /login and /register. */
export const guestGuard: CanActivateFn = (route) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  // Desktop only: honour ?redirect= so a deep-link (e.g. #/interview) survives
  // the login round-trip. The web build keeps the original /dashboard default,
  // so its behaviour is unchanged.
  const redirect =
    environment.electron && route.queryParamMap.get('redirect')
      ? route.queryParamMap.get('redirect')!
      : '/dashboard';
  const dest = router.parseUrl(redirect.startsWith('/') ? redirect : '/dashboard');

  if (auth.isAuthenticated) return dest;

  return auth.checkSession().pipe(map((user) => (user ? dest : true)));
};
