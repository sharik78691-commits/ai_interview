import { Injectable, computed, signal } from '@angular/core';
import { getScreenshotBridge } from './screenshot-bridge';
import { ScreenshotError, ScreenshotModel, ScreenshotUsage } from './screenshot.models';

/** Default model. Must match DEFAULT_MODEL_KEY in electron/screenshot/model-config.js. */
export const DEFAULT_SCREENSHOT_MODEL_KEY = 'gemini-3.5-flash-lite';

const STORAGE_KEY = 'aia_screenshot_model';

/** Shown until the main process reports the real catalog (and in unit tests). */
export const FALLBACK_MODELS: ScreenshotModel[] = [
  {
    key: DEFAULT_SCREENSHOT_MODEL_KEY,
    provider: 'google',
    displayName: 'Gemini 3.5 Flash Lite',
    limits: {
      maxImages: 10,
      maxImageBytes: 3 * 1024 * 1024,
      maxTotalBytes: 3 * 1024 * 1024,
      minDimension: 32,
      maxDimension: 8192,
    },
  },
];

function readSaved(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

/**
 * AI model selection + server readiness. There is no API key on the client:
 * the backend analyzes screenshots with the key from its own .env.
 */
@Injectable({ providedIn: 'root' })
export class ModelConfigService {
  readonly models = signal<ScreenshotModel[]>(FALLBACK_MODELS);
  readonly selectedKey = signal<string>(DEFAULT_SCREENSHOT_MODEL_KEY);
  /** true/false = server has / lacks its AI key; null = unknown (offline). */
  readonly serverReady = signal<boolean | null>(null);
  /** Screenshot credits left (from the backend); null = unknown. */
  readonly usage = signal<ScreenshotUsage | null>(null);

  readonly activeModel = computed(
    () =>
      this.models().find((m) => m.key === this.selectedKey()) ??
      this.models().find((m) => m.key === DEFAULT_SCREENSHOT_MODEL_KEY) ??
      this.models()[0],
  );
  /** Analysis is allowed unless the server said it is not set up. */
  readonly canUseServer = computed(() => this.serverReady() !== false);

  constructor() {
    this.selectedKey.set(this.resolveKey(readSaved(), this.models()));
  }

  /** A saved/unknown key falls back to the default model. */
  resolveKey(saved: string | null, models: ScreenshotModel[]): string {
    return saved && models.some((m) => m.key === saved) ? saved : DEFAULT_SCREENSHOT_MODEL_KEY;
  }

  async load(): Promise<void> {
    // This service outlives sign-out/sign-in (the desktop window never
    // reloads): drop the previous account's credits so e.g. an admin's
    // "unlimited" is never shown to the next user while (or if) this fails.
    this.usage.set(null);
    const bridge = getScreenshotBridge();
    if (!bridge) return;
    const res = await bridge.getConfig();
    if (!res.ok) throw new ScreenshotError(res.error.code, res.error.message);
    this.models.set(res.models.length ? res.models : FALLBACK_MODELS);
    this.serverReady.set(typeof res.serverReady === 'boolean' ? res.serverReady : null);
    this.usage.set(res.usage ?? null);
    this.selectedKey.set(this.resolveKey(readSaved(), this.models()));
  }

  select(key: string): void {
    this.selectedKey.set(this.resolveKey(key, this.models()));
    try {
      localStorage.setItem(STORAGE_KEY, this.selectedKey());
    } catch {
      // Private mode / storage disabled — selection still applies for this session.
    }
  }
}
