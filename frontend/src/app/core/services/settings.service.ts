import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { environment } from '../../../environments/environment';
import { AppSettings, ResponseLength } from '../models/interview.models';

const KEY = 'aia_settings';

const DEFAULTS: AppSettings = {
  aiModel: 'auto',
  sttProvider: 'browser',
  responseLength: 'default',
  theme: 'light', // replaced by defaultTheme() in load()
  micId: '',
  guidanceFontSize: 15,
  background: 'sunshine', // yellow call-to-action by default; 'default' = original green
  bgVersion: 2,
};

/** Allowed AI Guidance text sizes (px). */
export const FONT_SIZE_OPTIONS = [12, 13, 14, 15, 16, 17, 18, 20, 22, 24];
export const MIN_FONT_SIZE = 12;
export const MAX_FONT_SIZE = 24;

/** Clamp a stored font size into the allowed range. */
function clampFontSize(value: unknown): number {
  const n = typeof value === 'string' ? parseInt(value, 10) : (value as number);
  if (!Number.isFinite(n)) return DEFAULTS.guidanceFontSize;
  return Math.min(MAX_FONT_SIZE, Math.max(MIN_FONT_SIZE, Math.round(n)));
}

/** Legacy values stored by earlier builds. */
const LENGTH_ALIASES: Record<string, ResponseLength> = {
  default: 'default',
  complete: 'default',
  concise: 'short',
  brief: 'short',
  short: 'short',
  standard: 'default',
  medium: 'default',
  detailed: 'default',
  long: 'default',
};

/** Supported themes. 'darker' was a legacy settings option that never had a
    dedicated stylesheet, so it maps to 'dark'. Anything unset/unknown uses the
    build's default (light on the web, dark in the desktop app). */
export type ThemeName = 'dark' | 'light';

/** Default theme: the desktop (.exe) app starts dark, the website starts light. */
function defaultTheme(): ThemeName {
  return environment.electron ? 'dark' : 'light';
}

function normalizeTheme(value: unknown): ThemeName {
  const v = String(value ?? '').toLowerCase();
  if (v === 'dark' || v === 'darker') return 'dark';
  if (v === 'light') return 'light';
  return defaultTheme();
}

/** Site background presets. Each works in both themes: the CSS in styles.css
    maps `<html data-bg="…">` to a dark and a light palette. `swatch` is the
    preview color shown in Settings for each theme; `accent` is its button color. */
export const BACKGROUND_OPTIONS = [
  { key: 'sunshine', label: 'Sunshine yellow', swatch: { dark: '#14120a', light: '#fffbea' }, accent: '#ffc61a' },
  { key: 'calm', label: 'Calm white + orange', swatch: { dark: '#121212', light: '#ffffff' }, accent: '#f26a1b' },
  { key: 'default', label: 'Green (default)', swatch: { dark: '#07150e', light: '#ecf8f1' }, accent: '#137547' },
  { key: 'white', label: 'Black & white', swatch: { dark: '#000000', light: '#ffffff' }, accent: '#000000' },
  { key: 'slate', label: 'Slate gray', swatch: { dark: '#1e293b', light: '#dfe5ed' }, accent: '#137547' },
  { key: 'blue', label: 'Ocean blue', swatch: { dark: '#0b2447', light: '#cfe3fb' }, accent: '#137547' },
  { key: 'purple', label: 'Lavender', swatch: { dark: '#24124a', light: '#e2d6fa' }, accent: '#137547' },
  { key: 'rose', label: 'Rose', swatch: { dark: '#3d0f24', light: '#fad3e0' }, accent: '#137547' },
  { key: 'cream', label: 'Warm cream', swatch: { dark: '#33240f', light: '#f6e7c4' }, accent: '#137547' },
  { key: 'teal', label: 'Teal', swatch: { dark: '#063c40', light: '#c6ecec' }, accent: '#137547' },
] as const;

export type BackgroundName = (typeof BACKGROUND_OPTIONS)[number]['key'];

function normalizeBackground(value: unknown): BackgroundName {
  const v = String(value ?? '').toLowerCase();
  return BACKGROUND_OPTIONS.some((o) => o.key === v) ? (v as BackgroundName) : (DEFAULTS.background as BackgroundName);
}

/** Apply the background preset to <html>; 'default' keeps the theme's own colors. */
export function applyBackgroundToDom(background: unknown, doc: Document | undefined = globalDocument()): void {
  if (!doc) return;
  const bg = normalizeBackground(background);
  if (bg === 'default') doc.documentElement.removeAttribute('data-bg');
  else doc.documentElement.setAttribute('data-bg', bg);
}

/** Apply the theme to <html> so the CSS-variable layer switches instantly. */
function applyThemeToDom(theme: ThemeName, doc: Document | undefined = globalDocument()): void {
  if (!doc) return;
  doc.documentElement.setAttribute('data-theme', theme);
}

/** The browser document, or undefined where there is none. */
function globalDocument(): Document | undefined {
  return typeof document === 'undefined' ? undefined : document;
}

@Injectable({ providedIn: 'root' })
export class SettingsService {
  settings$ = new BehaviorSubject<AppSettings>(this.load());

  constructor() {
    // Apply the stored theme on startup (default light on web, dark on desktop; a saved choice is kept).
    this.applyToDocument();
  }

  /**
   * Write the current theme + background onto `<html>`. Prerendering passes
   * its server document (there is no global one) so static pages ship the
   * default colors instead of flashing other ones before the app starts.
   */
  applyToDocument(doc: Document | undefined = globalDocument()): void {
    applyThemeToDom(normalizeTheme(this.settings$.value.theme), doc);
    applyBackgroundToDom(this.settings$.value.background, doc);
  }

  private load(): AppSettings {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) {
        const parsed = JSON.parse(raw) as Partial<AppSettings>;
        return {
          ...DEFAULTS,
          ...parsed,
          responseLength:
            LENGTH_ALIASES[(parsed.responseLength ?? '').toLowerCase()] ?? DEFAULTS.responseLength,
          guidanceFontSize: clampFontSize(parsed.guidanceFontSize),
          theme: normalizeTheme(parsed.theme),
          // A background saved before the yellow default (no/old bgVersion)
          // moves to the current default once; later picks are kept.
          background:
            parsed.bgVersion === DEFAULTS.bgVersion
              ? normalizeBackground(parsed.background)
              : (DEFAULTS.background as BackgroundName),
          bgVersion: DEFAULTS.bgVersion,
        };
      }
    } catch {
      /* ignore */
    }
    return { ...DEFAULTS, theme: defaultTheme() };
  }

  get value(): AppSettings {
    return this.settings$.value;
  }

  /** Current theme ('dark' | 'light'). */
  get theme(): ThemeName {
    return normalizeTheme(this.settings$.value.theme);
  }

  /** Flip dark <-> light, persist it, and repaint immediately. */
  toggleTheme(): ThemeName {
    const next: ThemeName = this.theme === 'light' ? 'dark' : 'light';
    this.save({ theme: next });
    return next;
  }

  save(patch: Partial<AppSettings>): void {
    const next = { ...this.settings$.value, ...patch };
    localStorage.setItem(KEY, JSON.stringify(next));
    this.settings$.next(next);
    // Keep the page in sync whether the change came from Settings or the
    // navbar toggle button.
    applyThemeToDom(normalizeTheme(next.theme));
    applyBackgroundToDom(next.background);
  }

  reset(): void {
    const defaults: AppSettings = { ...DEFAULTS, theme: defaultTheme() };
    localStorage.setItem(KEY, JSON.stringify(defaults));
    this.settings$.next(defaults);
    applyThemeToDom(defaults.theme as ThemeName);
    applyBackgroundToDom(defaults.background);
  }
}
