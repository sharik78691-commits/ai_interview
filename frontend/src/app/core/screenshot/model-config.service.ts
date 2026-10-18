import { Injectable, computed, signal } from '@angular/core';
import { getScreenshotBridge } from './screenshot-bridge';
import { ScreenshotError, ScreenshotModel } from './screenshot.models';

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
      maxImages: 16,
      maxImageBytes: 7 * 1024 * 1024,
      maxTotalBytes: 14 * 1024 * 1024,
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

/** AI model selection + API-key status. The key itself is write-only from here. */
@Injectable({ providedIn: 'root' })
export class ModelConfigService {
  readonly models = signal<ScreenshotModel[]>(FALLBACK_MODELS);
  readonly selectedKey = signal<string>(DEFAULT_SCREENSHOT_MODEL_KEY);
  readonly keyStored = signal<Record<string, boolean>>({});
  readonly encryptionAvailable = signal(true);
  readonly keySources = signal<Record<string, 'stored' | 'env' | null>>({});
  readonly envHint = signal('');

  readonly activeModel = computed(
    () =>
      this.models().find((m) => m.key === this.selectedKey()) ??
      this.models().find((m) => m.key === DEFAULT_SCREENSHOT_MODEL_KEY) ??
      this.models()[0],
  );
  readonly hasKey = computed(() => !!this.keyStored()[this.activeModel().provider]);
  /** 'env' = loaded automatically from .env; 'stored' = saved in the app. */
  readonly keySource = computed(() => this.keySources()[this.activeModel().provider] ?? null);

  constructor() {
    this.selectedKey.set(this.resolveKey(readSaved(), this.models()));
  }

  /** A saved/unknown key falls back to the default model. */
  resolveKey(saved: string | null, models: ScreenshotModel[]): string {
    return saved && models.some((m) => m.key === saved) ? saved : DEFAULT_SCREENSHOT_MODEL_KEY;
  }

  async load(): Promise<void> {
    const bridge = getScreenshotBridge();
    if (!bridge) return;
    const res = await bridge.getConfig();
    if (!res.ok) throw new ScreenshotError(res.error.code, res.error.message);
    this.models.set(res.models.length ? res.models : FALLBACK_MODELS);
    this.keyStored.set(res.keyStored);
    this.keySources.set(res.keySources ?? {});
    this.envHint.set(res.envHint ?? '');
    this.encryptionAvailable.set(res.encryptionAvailable);
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

  async saveKey(apiKey: string): Promise<void> {
    const bridge = getScreenshotBridge();
    if (!bridge) throw new ScreenshotError('NOT_DESKTOP', 'Only available in the desktop app.');
    const model = this.activeModel();
    const res = await bridge.setKey(model.key, apiKey);
    if (!res.ok) throw new ScreenshotError(res.error.code, res.error.message);
    this.keyStored.update((k) => ({ ...k, [model.provider]: true }));
    this.keySources.update((k) => ({ ...k, [model.provider]: 'stored' }));
  }

  async clearKey(): Promise<void> {
    const bridge = getScreenshotBridge();
    if (!bridge) return;
    const model = this.activeModel();
    const res = await bridge.clearKey(model.key);
    if (!res.ok) throw new ScreenshotError(res.error.code, res.error.message);
    // A .env key may still apply after the saved key is removed — ask the main process.
    await this.load();
  }
}
