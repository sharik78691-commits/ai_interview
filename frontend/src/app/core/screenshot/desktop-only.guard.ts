import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { isScreenCaptureAvailable } from './screenshot-bridge';

/** Blocks the screenshot route everywhere except the packaged desktop app. */
export const desktopOnlyGuard: CanActivateFn = () =>
  isScreenCaptureAvailable() ? true : inject(Router).createUrlTree(['/dashboard']);
