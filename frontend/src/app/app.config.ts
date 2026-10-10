import { ApplicationConfig, ErrorHandler, provideZoneChangeDetection } from '@angular/core';
import { provideRouter, withHashLocation } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { routes } from './app.routes';
import { environment } from '../environments/environment';
import { authInterceptor } from './core/auth/auth.interceptor';
import { errorLoggingInterceptor } from './core/errors/error-logging.interceptor';
import { GlobalErrorHandler } from './core/errors/global-error-handler';

export const appConfig: ApplicationConfig = {
  providers: [
    provideZoneChangeDetection({ eventCoalescing: true }),
    // file:// has no real /interview path. Hash URLs (#/interview) keep routing
    // inside the packaged desktop app.
    provideRouter(routes, ...(environment.electron ? [withHashLocation()] : [])),
    // The auth interceptor attaches the session cookie + CSRF header and
    // handles expired sessions. The error-logging interceptor runs after it and
    // records every failed request for diagnosis.
    provideHttpClient(withInterceptors([authInterceptor, errorLoggingInterceptor])),
    // Catch uncaught Angular errors globally instead of letting them vanish.
    { provide: ErrorHandler, useClass: GlobalErrorHandler },
  ],
};
