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
};

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

@Injectable({ providedIn: 'root' })
export class SettingsService {
  settings$ = new BehaviorSubject<AppSettings>(this.load());

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

  save(patch: Partial<AppSettings>): void {
    const next = { ...this.settings$.value, ...patch };
    localStorage.setItem(KEY, JSON.stringify(next));
    this.settings$.next(next);
  }

  reset(): void {
    localStorage.setItem(KEY, JSON.stringify(DEFAULTS));
    this.settings$.next({ ...DEFAULTS });
  }
}
