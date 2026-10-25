import { RenderMode } from '@angular/ssr';
import { routes } from './app.routes';
import { PRERENDERED_PATHS, serverRoutes } from './app.routes.server';
import { authGuard } from './core/auth/auth.guard';
import { SeoConfig } from './core/services/seo.service';

describe('serverRoutes (prerendering)', () => {
  const byPath = new Map(routes.map((r) => [r.path, r]));

  it('prerenders only real, indexable, public pages', () => {
    for (const path of PRERENDERED_PATHS) {
      const route = byPath.get(path);
      expect(route).withContext(path).toBeDefined();
      expect(route?.redirectTo).withContext(path).toBeUndefined();
      expect((route?.data?.['seo'] as SeoConfig | undefined)?.noindex).withContext(path).toBeFalsy();
      expect(route?.canActivate ?? []).withContext(path).not.toContain(authGuard);
    }
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
