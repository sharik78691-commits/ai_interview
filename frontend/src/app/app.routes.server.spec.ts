import { RenderMode } from '@angular/ssr';
import { routes } from './app.routes';
import { PRERENDERED_PATHS, serverRoutes } from './app.routes.server';
import { authGuard } from './core/auth/auth.guard';
import { SeoConfig } from './core/services/seo.service';

describe('serverRoutes (prerendering)', () => {
  const byPath = new Map(routes.map((r) => [r.path, r]));

  // Prerendered for a fast first paint, but thin form pages kept out of the index.
  const NOINDEX_PRERENDERED = new Set(['login', 'register']);

  it('prerenders only real, public pages — indexable except the auth forms', () => {
    for (const path of PRERENDERED_PATHS) {
      const route = byPath.get(path);
      expect(route).withContext(path).toBeDefined();
      expect(route?.redirectTo).withContext(path).toBeUndefined();
      const noindex = (route?.data?.['seo'] as SeoConfig | undefined)?.noindex;
      if (NOINDEX_PRERENDERED.has(path)) expect(noindex).withContext(path).toBeTrue();
      else expect(noindex).withContext(path).toBeFalsy();
      expect(route?.canActivate ?? []).withContext(path).not.toContain(authGuard);
    }
  });

  it('serves unknown URLs a noindex "not found" page on the web (no soft-404 home copies)', () => {
    const wildcard = routes[routes.length - 1];
    expect(wildcard.path).toBe('**');
    expect(wildcard.redirectTo).toBeUndefined();
    expect(wildcard.loadComponent).toBeDefined();
    expect((wildcard.data?.['seo'] as SeoConfig).noindex).toBeTrue();
  });

  it('keeps every signed-in page client-rendered', () => {
    const prerendered = new Set<string>(PRERENDERED_PATHS);
    const privatePaths = routes
      .filter((r) => r.canActivate?.includes(authGuard))
      .map((r) => r.path as string);
    expect(privatePaths.length).toBeGreaterThan(0);
    for (const path of privatePaths) expect(prerendered.has(path)).withContext(path).toBeFalse();

    const fallback = serverRoutes[serverRoutes.length - 1];
    expect(fallback.path).toBe('**');
    expect(fallback.renderMode).toBe(RenderMode.Client);
  });
});

describe('home page SEO', () => {
  it('targets "AI interview assistant" in the title and description', () => {
    const seo = routes.find((r) => r.path === '')?.data?.['seo'] as SeoConfig;
    expect(seo.title).toContain('AI Interview Assistant');
    expect(seo.description.toLowerCase()).toContain('ai interview assistant');
    expect(seo.title.length).toBeLessThanOrEqual(65);
    expect(seo.description.length).toBeLessThanOrEqual(160);
  });
});

describe('keyword landing pages SEO', () => {
  const slugs = routes
    .filter((r) => r.data?.['page'])
    .map((r) => r.path as string);

  it('registers, prerenders and indexes every keyword page', () => {
    expect(slugs.length).toBeGreaterThanOrEqual(6);
    const prerendered = new Set(serverRoutes.map((r) => r.path));
    for (const slug of slugs) {
      expect(prerendered.has(slug)).withContext(slug).toBeTrue();
      const route = routes.find((r) => r.path === slug)!;
      const seo = route.data!['seo'] as SeoConfig;
      expect(seo.path).withContext(slug).toBe(`/${slug}`);
      expect(seo.noindex).withContext(slug).toBeFalsy();
      expect(seo.title.length).withContext(slug).toBeLessThanOrEqual(70);
      expect(seo.description.length).withContext(slug).toBeLessThanOrEqual(160);
    }
  });

  it('gives every keyword page a unique title and description', () => {
    const seos = slugs.map((s) => routes.find((r) => r.path === s)!.data!['seo'] as SeoConfig);
    expect(new Set(seos.map((s) => s.title)).size).toBe(seos.length);
    expect(new Set(seos.map((s) => s.description)).size).toBe(seos.length);
  });

  it('has a pillar page targeting the head keyword "AI interview"', () => {
    const seo = routes.find((r) => r.path === 'ai-interview')?.data?.['seo'] as SeoConfig;
    expect(seo).toBeDefined();
    expect(seo.title.startsWith('AI Interview')).toBeTrue();
    expect(seo.description.toLowerCase()).toContain('ai interview');
  });
});
