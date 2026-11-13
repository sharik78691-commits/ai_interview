import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';

/**
 * Web-only "page not found" view for unknown URLs.
 *
 * Previously every unknown URL redirected to the home page, so search engines
 * saw copies of the home page at made-up URLs ("soft 404"). This page is
 * marked noindex via the route's SEO data so those URLs drop out of the index,
 * while visitors still get clear links back into the site.
 * The desktop (Electron) build keeps the old redirect — see app.routes.ts.
 */
@Component({
  selector: 'app-not-found',
  standalone: true,
  imports: [RouterLink],
  template: `
    <div class="container info">
      <header class="info-head">
        <h1>Page not found</h1>
        <p class="lead">The page you're looking for doesn't exist or has moved.</p>
      </header>

      <section>
        <ul class="links">
          <li><a routerLink="/" class="btn-primary">Go to the home page</a></li>
          <li><a routerLink="/ai-interview-assistant">AI interview assistant</a></li>
          <li><a routerLink="/pricing">Pricing</a></li>
          <li><a routerLink="/contact">Contact us</a></li>
        </ul>
      </section>
    </div>
  `,
  styles: [
    `
      .links { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; justify-content: center; list-style: none; padding: 0; }
    `,
  ],
})
export class NotFoundComponent {}
