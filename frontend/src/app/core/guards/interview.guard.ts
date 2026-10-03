import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

export const interviewGuard: CanActivateFn = () => {
  const router = inject(Router);
  const hasCtx =
    (localStorage.getItem('aia_resumeText') ?? '').trim().length > 0 &&
    (localStorage.getItem('aia_jd') ?? '').trim().length > 0;
  const demo = localStorage.getItem('aia_demo') === '1';
  if (hasCtx || demo) return true;
  return router.createUrlTree(['/dashboard']);
};
