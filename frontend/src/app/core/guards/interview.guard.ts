import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthService } from '../auth/auth.service';

/**
 * Allows entry to the interview screen.
 *
 * Resume and job description are OPTIONAL, so a user may start an interview
 * directly. The only requirement is an authenticated session (enforced by the
 * authGuard that runs before this one, and by the backend).
 */
export const interviewGuard: CanActivateFn = () => {
  const router = inject(Router);
  const auth = inject(AuthService);
  if (auth.isAuthenticated) return true;
  return router.createUrlTree(['/login']);
};
