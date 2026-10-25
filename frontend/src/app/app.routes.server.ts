import { RenderMode, ServerRoute } from '@angular/ssr';

/**
 * Public, indexable pages rendered to static HTML at build time, so each one
 * ships its own title, description, canonical link and body text instead of
 * the empty `<app-root>` shell. Keep in sync with public/sitemap.xml and the
 * rewrites in vercel.json.
 */
export const PRERENDERED_PATHS = [
  '',
  'login',
  'register',
  'features',
  'how-it-works',
  'pricing',
  'trust',
  'contact',
  'about',
  'terms',
  'acceptable-use',
  'privacy-policy',
  'refund-policy',
  'shipping-policy',
] as const;

export const serverRoutes: ServerRoute[] = [
  ...PRERENDERED_PATHS.map((path) => ({ path, renderMode: RenderMode.Prerender as const })),
  // Signed-in app pages (dashboard, interview, settings, ...) stay client-rendered
  // from index.csr.html, exactly as before.
  { path: '**', renderMode: RenderMode.Client },
];
