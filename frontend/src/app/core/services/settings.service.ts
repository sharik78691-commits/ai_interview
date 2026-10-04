import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { AppSettings, ResponseLength } from '../models/interview.models';

const KEY = 'aia_settings';

const DEFAULTS: AppSettings = {
  aiModel: 'auto',
  sttProvider: 'browser',
  responseLength: 'medium',
  theme: 'dark',
  micId: '',
  guidanceFontSize: 15,
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
  concise: 'short',
  brief: 'short',
  short: 'short',
  standard: 'medium',
  medium: 'medium',
  detailed: 'long',
  long: 'long',
};

/** Supported themes. 'darker' was a legacy settings option that never had a
    dedicated stylesheet, so it maps to 'dark'. */
export type ThemeName = 'dark' | 'light';

function normalizeTheme(value: unknown): ThemeName {
  return String(value ?? '').toLowerCase() === 'light' ? 'light' : 'dark';
}

/** Apply the theme to <html> so the CSS-variable layer switches instantly. */
function applyThemeToDom(theme: ThemeName): void {
  if (typeof document === 'undefined') return;
  document.documentElement.dataset['theme'] = theme;
}

@Injectable({ providedIn: 'root' })
export class SettingsService {
  settings$ = new BehaviorSubject<AppSettings>(this.load());

  constructor() {
    // Apply the stored theme on startup (default dark, unchanged behaviour).
    applyThemeToDom(normalizeTheme(this.settings$.value.theme));
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
        };
      }
    } catch {
      /* ignore */
    }
    return { ...DEFAULTS };
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
  }

  reset(): void {
    localStorage.setItem(KEY, JSON.stringify(DEFAULTS));
    this.settings$.next({ ...DEFAULTS });
    applyThemeToDom(normalizeTheme(DEFAULTS.theme));
  }
}
