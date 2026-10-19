import { ApplicationConfig, ErrorHandler, provideZoneChangeDetection } from '@angular/core';
import { provideRouter, withHashLocation, withPreloading } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { routes } from './app.routes';
import { environment } from '../environments/environment';
import { authInterceptor } from './core/auth/auth.interceptor';
import { errorLoggingInterceptor } from './core/errors/error-logging.interceptor';
import { rateLimitRetryInterceptor } from './core/errors/rate-limit-retry.interceptor';
import { GlobalErrorHandler } from './core/errors/global-error-handler';
import { SelectivePreloadingStrategy } from './core/routing/selective-preloading.strategy';

export const appConfig: ApplicationConfig = {
  providers: [
    provideZoneChangeDetection({ eventCoalescing: true }),
    // file:// has no real /interview path. Hash URLs (#/interview) keep routing
    // inside the packaged desktop app.
    provideRouter(
      routes,
      // Warm the lazy core-flow chunks (data.preload) after first paint.
      withPreloading(SelectivePreloadingStrategy),
      ...(environment.electron ? [withHashLocation()] : []),
    ),
    // The auth interceptor attaches the session cookie + CSRF header and
    // handles expired sessions. The error-logging interceptor runs after it and
    // records every failed request for diagnosis. Innermost, a rate-limited
    // (429) GET is retried once after Retry-After, so only a final failure is
    // logged and shown.
    provideHttpClient(
      withInterceptors([authInterceptor, errorLoggingInterceptor, rateLimitRetryInterceptor]),
    ),
    // Catch uncaught Angular errors globally instead of letting them vanish.
    { provide: ErrorHandler, useClass: GlobalErrorHandler },
  ],
};
