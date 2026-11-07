import { DOCUMENT } from '@angular/common';
import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { SEO_CONTENT, SeoPageContent } from './seo-content';
import { SEO_PAGES, SeoPageMeta } from './seo-pages';

const SITE_ORIGIN = 'https://www.oyeinterview.com';
const LD_ID = 'seo-landing-ld';

/**
 * Keyword landing page (/ai-interview-assistant, /ai-mock-interview, ...).
 * Content comes from SEO_PAGES keyed by the route's `data.page`. Prerendered
 * to static HTML so crawlers see the full text and the BreadcrumbList JSON-LD.
 */
@Component({
  selector: 'app-seo-landing',
  standalone: true,
  imports: [RouterLink],
  template: `
    <article class="container info seo-page">
      <nav class="crumbs muted small" aria-label="Breadcrumb">
        <a routerLink="/">Home</a> <span aria-hidden="true">›</span> <span>{{ meta.label }}</span>
      </nav>

      <header class="info-head">
        <h1>{{ page.h1 }}</h1>
        <p class="lead">{{ page.lead }}</p>
        <div class="cta-row">
          <a routerLink="/register" class="btn-primary">Try OyeInterview free</a>
          <a routerLink="/pricing" class="btn-ghost">See pricing</a>
        </div>
      </header>

      @for (s of page.sections; track s.heading) {
        <section>
          <h2>{{ s.heading }}</h2>
          @for (p of s.paragraphs; track $index) {
            <p class="body">{{ p }}</p>
          }
          @if (s.bullets?.length) {
            <ul class="ticks">
              @for (b of s.bullets; track $index) {
                <li>{{ b }}</li>
              }
            </ul>
          }
        </section>
      }

      <section>
        <h2>Frequently asked questions</h2>
        @for (f of page.faq; track f.q) {
          <details class="faq">
            <summary>{{ f.q }}</summary>
            <p class="body">{{ f.a }}</p>
          </details>
        }
      </section>

      <section class="callout">
        <p>
          <strong>Good to know:</strong> AI answers can be wrong — check them before you rely on
          them, and follow the rules of every interview process.
          <a routerLink="/acceptable-use">Acceptable use</a>.
        </p>
      </section>

      <section>
        <h2>Explore more</h2>
        <ul class="related">
          @for (r of related; track r.slug) {
            <li><a [routerLink]="'/' + r.slug">{{ r.label }}</a></li>
          }
          <li><a routerLink="/features">All features</a></li>
          <li><a routerLink="/how-it-works">How it works</a></li>
        </ul>
      </section>

      <div class="cta-row">
        <a routerLink="/register" class="btn-primary">Start your first session</a>
      </div>
    </article>
  `,
  styles: [
    `
      .seo-page { max-width: 860px; }
      .crumbs { display: flex; gap: 8px; align-items: center; }
      .crumbs a { color: var(--muted); }
      .crumbs a:hover { color: var(--accent); }
      .body { line-height: 1.75; margin-bottom: 12px; opacity: .92; }
      .ticks { display: grid; gap: 8px; padding-left: 20px; line-height: 1.6; }
      .cta-row { display: flex; flex-wrap: wrap; gap: 12px; justify-content: center; margin-top: 20px; }
      .faq { border: 1px solid var(--border); border-radius: 12px; padding: 12px 16px; margin-bottom: 10px; }
      .faq summary { cursor: pointer; font-weight: 700; }
      .faq p { margin: 10px 0 0; }
      .related { display: flex; flex-wrap: wrap; gap: 10px; list-style: none; padding: 0; }
      .related a { display: inline-block; border: 1px solid var(--border); border-radius: 999px; padding: 6px 14px; font-size: .9rem; }
      .related a:hover { color: var(--accent); border-color: var(--accent); }
    `,
  ],
})
export class SeoLandingComponent implements OnInit, OnDestroy {
  private doc = inject(DOCUMENT);
  private route = inject(ActivatedRoute);

  private readonly slug = this.route.snapshot.data['page'] as string;
  readonly meta: SeoPageMeta = SEO_PAGES.find((p) => p.slug === this.slug)!;
  readonly page: SeoPageContent = SEO_CONTENT[this.slug];
  readonly related: SeoPageMeta[] = SEO_PAGES.filter((p) => p.slug !== this.slug);

  ngOnInit(): void {
    const url = `${SITE_ORIGIN}/${this.page.slug}`;
    const ld = {
      '@context': 'https://schema.org',
      '@graph': [
        {
          '@type': 'WebPage',
          name: this.meta.title,
          description: this.meta.description,
          url,
          isPartOf: { '@type': 'WebSite', name: 'OyeInterview', url: `${SITE_ORIGIN}/` },
        },
        {
          '@type': 'BreadcrumbList',
          itemListElement: [
            { '@type': 'ListItem', position: 1, name: 'Home', item: `${SITE_ORIGIN}/` },
            { '@type': 'ListItem', position: 2, name: this.meta.label, item: url },
          ],
        },
      ],
    };
    this.removeLd();
    const script = this.doc.createElement('script');
    script.setAttribute('type', 'application/ld+json');
    script.id = LD_ID;
    script.textContent = JSON.stringify(ld);
    this.doc.head.appendChild(script);
  }

  ngOnDestroy(): void {
    this.removeLd();
  }

  private removeLd(): void {
    this.doc.getElementById(LD_ID)?.remove();
  }
}
