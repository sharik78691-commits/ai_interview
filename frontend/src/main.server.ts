import { BootstrapContext, bootstrapApplication } from '@angular/platform-browser';
import { App } from './app/app';
import { config } from './app/app.config.server';

/**
 * Build-time (prerender) entry only. Production web builds render the public
 * marketing pages to static HTML so crawlers see real content; there is no
 * Node server at runtime. See app.routes.server.ts for which pages.
 */
const bootstrap = (context: BootstrapContext) => bootstrapApplication(App, config, context);

export default bootstrap;
