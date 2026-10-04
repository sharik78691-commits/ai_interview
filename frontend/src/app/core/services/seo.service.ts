import { DOCUMENT } from '@angular/common';
import { Injectable, inject } from '@angular/core';
import { Meta, Title } from '@angular/platform-browser';

/** Per-page SEO metadata applied when a route activates. */
export interface SeoConfig {
  /** <title> and og:title / twitter:title. */
  title: string;
  /** Meta description and og:description / twitter:description. */
  description: string;
  /** Path appended to the canonical origin, e.g. "/" or "/login". */
  path: string;
  /** When true, adds noindex so the page is kept out of search results. */
  noindex?: boolean;
}

/** Canonical production origin. All canonical URLs are built from this. */
const SITE_ORIGIN = 'https://www.oyeinterview.com';

/**
 * Central SEO helper for the single-page app.
 *
 * An Angular SPA ships one `index.html`, so every route would otherwise share
 * the same title/description. This service rewrites the document head on each
 * navigation so crawlers and social scrapers see route-specific metadata, and
 * keeps a single canonical URL per page to avoid duplicate-content penalties.
 */
@Injectable({ providedIn: 'root' })
export class SeoService {
  private title = inject(Title);
  private meta = inject(Meta);
  private doc = inject(DOCUMENT);

  /** Apply a page's title, description, canonical link and robots directive. */
  apply(config: SeoConfig): void {
    const url = `${SITE_ORIGIN}${config.path}`;

    this.title.setTitle(config.title);
    this.setTag('name', 'description', config.description);

    // Open Graph + Twitter mirrors so shared links render correctly.
    this.setTag('property', 'og:title', config.title);
    this.setTag('property', 'og:description', config.description);
    this.setTag('property', 'og:url', url);
    this.setTag('name', 'twitter:title', config.title);
    this.setTag('name', 'twitter:description', config.description);

    // Robots: keep private app pages out of the index.
    this.setTag('name', 'robots', config.noindex ? 'noindex, nofollow' : 'index, follow');

    this.setCanonical(url);
  }

  /** Create or update a <meta> tag identified by attribute + value. */
  private setTag(attr: 'name' | 'property', key: string, content: string): void {
    const selector = `${attr}="${key}"`;
    if (this.meta.getTag(selector)) {
      this.meta.updateTag({ [attr]: key, content });
    } else {
      this.meta.addTag({ [attr]: key, content });
    }
  }

  /** Point the <link rel="canonical"> at the given absolute URL. */
  private setCanonical(url: string): void {
    let link = this.doc.querySelector<HTMLLinkElement>('link[rel="canonical"]');
    if (!link) {
      link = this.doc.createElement('link');
      link.setAttribute('rel', 'canonical');
      this.doc.head.appendChild(link);
    }
    link.setAttribute('href', url);
  }
}
