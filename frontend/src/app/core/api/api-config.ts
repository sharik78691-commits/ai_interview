import { InjectionToken } from '@angular/core';
import { environment } from '../../../environments/environment';

/**
 * Origin prefix for every REST call (`${base}/api/...`).
 *
 * '' in web builds (same-origin: the dev-server proxy locally, the Vercel
 * rewrite in production) and an absolute URL in the desktop build, where the
 * app runs under a local scheme with no proxy. Injected rather than read from
 * `environment` in each service so specs and alternative hosts can override
 * it with a single provider.
 */
export const API_BASE_URL = new InjectionToken<string>('API_BASE_URL', {
  providedIn: 'root',
  factory: () => environment.apiBaseUrl,
});
