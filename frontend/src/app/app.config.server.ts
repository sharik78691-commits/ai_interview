import { ApplicationConfig, inject, mergeApplicationConfig, provideAppInitializer } from '@angular/core';
import { DOCUMENT } from '@angular/common';
import { HttpBackend } from '@angular/common/http';
import { provideServerRendering, withRoutes } from '@angular/ssr';
import { appConfig } from './app.config';
import { serverRoutes } from './app.routes.server';
import { SettingsService } from './core/services/settings.service';
import { PrerenderHttpBackend } from './core/ssr/prerender-http-backend';

const serverConfig: ApplicationConfig = {
  providers: [
    provideServerRendering(withRoutes(serverRoutes)),
    // Prerendering runs at build time with no backend: answer the session check
    // as "signed out" and leave every other API call pending, so pages render
    // their anonymous / loading state and the browser fetches live data.
    { provide: HttpBackend, useClass: PrerenderHttpBackend },
    // Bake the default theme + background into <html> of every static page.
    provideAppInitializer(() => inject(SettingsService).applyToDocument(inject(DOCUMENT))),
  ],
};

export const config = mergeApplicationConfig(appConfig, serverConfig);
