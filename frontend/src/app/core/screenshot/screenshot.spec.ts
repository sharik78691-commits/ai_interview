import { TestBed } from '@angular/core/testing';
import { Router, UrlTree, provideRouter } from '@angular/router';
import { environment } from '../../../environments/environment';
import { splitFences } from '../../features/screenshot-analysis/analysis-result.component';
import { AnalysisHistoryService, MAX_HISTORY } from './analysis-history.service';
import { desktopOnlyGuard } from './desktop-only.guard';
import { validateImages } from './image-validation';
import {
  DEFAULT_SCREENSHOT_MODEL_KEY,
  FALLBACK_MODELS,
  ModelConfigService,
} from './model-config.service';
import { ScreenCaptureService } from './screen-capture.service';
import { ScreenshotAnalysisService } from './screenshot-analysis.service';
import { ScreenshotBridge, getScreenshotBridge, isScreenCaptureAvailable } from './screenshot-bridge';
import { AnalysisResult, CapturedImage, ScreenshotError } from './screenshot.models';

export const RESULT: AnalysisResult = {
  questions: ['What is a closure?'],
  topic: 'JavaScript',
  seniority: 'Mid',
  directAnswer: 'A function with its lexical scope.',
  interviewAnswer: 'A closure is...',
  keyPoints: ['lexical scope'],
  followUps: [{ question: 'Leaks?', suggestedResponse: 'Possibly.' }],
  code: null,
};

export const b64 = (bytes: number) => btoa('a'.repeat(bytes));
export const shot = (bytes = 300, extra: Partial<CapturedImage> = {}): CapturedImage => ({
  mimeType: 'image/png',
  data: b64(bytes),
  width: 800,
  height: 600,
  sourceName: 'Entire Screen',
  ...extra,
});

/** Installs a fake desktop bridge and the desktop flag; returns a restore fn. */
export function installDesktopBridge(overrides: Partial<ScreenshotBridge> = {}) {
  const bridge: ScreenshotBridge = {
    getConfig: async () => ({
      ok: true,
      models: FALLBACK_MODELS,
      defaultModel: DEFAULT_SCREENSHOT_MODEL_KEY,
      keyStored: { google: true },
      encryptionAvailable: true,
    }),
    listSources: async () => ({
      ok: true,
      sources: [{ id: 'screen:1:0', name: 'Entire Screen', kind: 'screen', thumbnail: 'data:image/png;base64,AAAA' }],
    }),
    capture: async () => ({ ok: true, image: shot() }),
    setKey: async () => ({ ok: true }),
    clearKey: async () => ({ ok: true }),
    analyze: async () => ({ ok: true, result: RESULT, model: 'Gemini 3.5 Flash Lite' }),
    ...overrides,
  };
  const prevFlag = environment.electron;
  const prevApi = window.electronAPI;
  environment.electron = true;
  window.electronAPI = { isElectron: true, screenshot: bridge } as unknown as typeof window.electronAPI;
  return () => {
    environment.electron = prevFlag;
    window.electronAPI = prevApi;
  };
}

const LIMITS = FALLBACK_MODELS[0].limits;

describe('desktop-only gating', () => {
  let restore: (() => void) | null = null;
  afterEach(() => {
    restore?.();
    restore = null;
    TestBed.resetTestingModule();
  });

  it('is unavailable in the web build (flag off)', () => {
    expect(environment.electron).toBeFalse();
    expect(isScreenCaptureAvailable()).toBeFalse();
    expect(getScreenshotBridge()).toBeNull();
  });

  it('stays unavailable if a bridge is faked in a browser without the desktop flag', () => {
    const prev = window.electronAPI;
    window.electronAPI = { isElectron: true, screenshot: {} } as unknown as typeof window.electronAPI;
    try {
      expect(isScreenCaptureAvailable()).toBeFalse();
    } finally {
      window.electronAPI = prev;
    }
  });

  it('is available in the desktop build with the preload bridge', () => {
    restore = installDesktopBridge();
    expect(isScreenCaptureAvailable()).toBeTrue();
  });

  it('is unavailable in the desktop flag without a bridge', () => {
    const prev = environment.electron;
    environment.electron = true;
    try {
      expect(isScreenCaptureAvailable()).toBeFalse();
    } finally {
      environment.electron = prev;
    }
  });

  it('route guard redirects web users to /dashboard and lets desktop through', () => {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
    const run = () => TestBed.runInInjectionContext(() => desktopOnlyGuard({} as never, {} as never));
    const web = run() as UrlTree;
    expect(TestBed.inject(Router).serializeUrl(web)).toBe('/dashboard');
    restore = installDesktopBridge();
    expect(run()).toBeTrue();
  });

  it('capture and analysis services refuse to run on the web', async () => {
    const capture = TestBed.inject(ScreenCaptureService);
    expect(capture.available).toBeFalse();
    await expectAsync(capture.listSources()).toBeRejectedWithError(/desktop app/);
    await expectAsync(capture.capture('screen:1:0')).toBeRejectedWithError(/desktop app/);
    await expectAsync(TestBed.inject(ScreenshotAnalysisService).analyze([shot()])).toBeRejectedWithError(/desktop app/);
  });
});

describe('model default selection', () => {
  beforeEach(() => localStorage.removeItem('aia_screenshot_model'));
  afterEach(() => {
    localStorage.removeItem('aia_screenshot_model');
    TestBed.resetTestingModule();
  });

  it('defaults to Gemini 3.5 Flash Lite and shows it as the active model', () => {
    const cfg = TestBed.inject(ModelConfigService);
    expect(DEFAULT_SCREENSHOT_MODEL_KEY).toBe('gemini-3.5-flash-lite');
    expect(cfg.selectedKey()).toBe('gemini-3.5-flash-lite');
    expect(cfg.activeModel().displayName).toBe('Gemini 3.5 Flash Lite');
  });

  it('falls back to the default for unknown saved/selected keys', () => {
    localStorage.setItem('aia_screenshot_model', 'retired-model');
    const cfg = TestBed.inject(ModelConfigService);
    expect(cfg.selectedKey()).toBe(DEFAULT_SCREENSHOT_MODEL_KEY);
    cfg.select('also-unknown');
    expect(cfg.selectedKey()).toBe(DEFAULT_SCREENSHOT_MODEL_KEY);
  });

  it('supports additional models from the catalog (extensible)', async () => {
    const restore = installDesktopBridge({
      getConfig: async () => ({
        ok: true,
        models: [...FALLBACK_MODELS, { key: 'future-model', provider: 'acme', displayName: 'Future', limits: LIMITS }],
        defaultModel: DEFAULT_SCREENSHOT_MODEL_KEY,
        keyStored: {},
        encryptionAvailable: true,
      }),
    });
    try {
      const cfg = TestBed.inject(ModelConfigService);
      await cfg.load();
      cfg.select('future-model');
      expect(cfg.activeModel().displayName).toBe('Future');
      expect(cfg.hasKey()).toBeFalse();
    } finally {
      restore();
    }
  });

  it('tracks key presence without ever exposing the key', async () => {
    let sent = '';
    const restore = installDesktopBridge({
      getConfig: async () => ({ ok: true, models: FALLBACK_MODELS, defaultModel: DEFAULT_SCREENSHOT_MODEL_KEY, keyStored: {}, encryptionAvailable: true }),
      setKey: async (_m, k) => ((sent = k), { ok: true }),
    });
    try {
      const cfg = TestBed.inject(ModelConfigService);
      await cfg.load();
      expect(cfg.hasKey()).toBeFalse();
      await cfg.saveKey('AIzaSECRET_KEY_123456');
      expect(sent).toBe('AIzaSECRET_KEY_123456');
      expect(cfg.hasKey()).toBeTrue();
      expect(JSON.stringify(cfg.keyStored())).not.toContain('AIza');
      await cfg.clearKey();
      expect(cfg.hasKey()).toBeFalse();
    } finally {
      restore();
    }
  });
});

describe('multi-image validation', () => {
  const codes = (imgs: CapturedImage[]) => validateImages(imgs, LIMITS).errors.map((e) => e.code);

  it('accepts several valid screenshots up to the model maximum', () => {
    const imgs = Array.from({ length: LIMITS.maxImages }, () => shot(1000));
    expect(validateImages(imgs, LIMITS).ok).toBeTrue();
  });

  it('rejects an empty selection and more than the maximum count', () => {
    expect(codes([])).toEqual(['EMPTY']);
    expect(codes(Array.from({ length: LIMITS.maxImages + 1 }, () => shot(10)))).toContain('TOO_MANY');
  });

  it('rejects oversize images, oversize total, bad type and bad dimensions', () => {
    expect(codes([shot(LIMITS.maxImageBytes + 10)])).toContain('IMAGE_TOO_LARGE');
    const big = Math.floor(LIMITS.maxImageBytes * 0.9);
    expect(codes([shot(big), shot(big), shot(big)])).toContain('TOTAL_TOO_LARGE');
    expect(codes([shot(10, { mimeType: 'image/gif' })])).toContain('BAD_TYPE');
    expect(codes([shot(10, { width: 5, height: 5 })])).toContain('DIMENSIONS_TOO_SMALL');
    expect(codes([shot(10, { width: LIMITS.maxDimension + 1 })])).toContain('DIMENSIONS_TOO_LARGE');
  });

  it('points at the offending screenshot', () => {
    const res = validateImages([shot(10), shot(10, { mimeType: 'text/plain' })], LIMITS);
    expect(res.errors[0].index).toBe(1);
    expect(res.errors[0].message).toContain('Screenshot 2');
  });
});

describe('analysis service', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('validates before sending and surfaces main-process errors', async () => {
    let calls = 0;
    const restore = installDesktopBridge({
      analyze: async () => (calls++, { ok: false, error: { code: 'RATE_LIMITED', message: 'Rate limit reached.' } }),
    });
    try {
      const svc = TestBed.inject(ScreenshotAnalysisService);
      await expectAsync(svc.analyze([])).toBeRejectedWith(jasmine.objectContaining({ code: 'VALIDATION' }));
      expect(calls).toBe(0);
      await expectAsync(svc.analyze([shot()])).toBeRejectedWith(
        jasmine.objectContaining({ code: 'RATE_LIMITED', message: 'Rate limit reached.' }),
      );
      expect(calls).toBe(1);
    } finally {
      restore();
    }
  });

  it('sends images in order without the source name', async () => {
    let sent: unknown[] = [];
    const restore = installDesktopBridge({ analyze: async (_k, imgs) => ((sent = imgs), { ok: true, result: RESULT, model: 'm' }) });
    try {
      const out = await TestBed.inject(ScreenshotAnalysisService).analyze([shot(10, { width: 801 }), shot(10, { width: 802 })]);
      expect(out.result.topic).toBe('JavaScript');
      expect((sent as { width: number }[]).map((i) => i.width)).toEqual([801, 802]);
      expect(JSON.stringify(sent)).not.toContain('sourceName');
    } finally {
      restore();
    }
  });
});

describe('capture service', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('lists sources and captures, mapping failures to ScreenshotError', async () => {
    const restore = installDesktopBridge({
      capture: async (id) =>
        id === 'bad' ? { ok: false, error: { code: 'SOURCE_GONE', message: 'gone' } } : { ok: true, image: shot() },
    });
    try {
      const svc = TestBed.inject(ScreenCaptureService);
      expect((await svc.listSources()).length).toBe(1);
      expect((await svc.capture('screen:1:0')).mimeType).toBe('image/png');
      await expectAsync(svc.capture('bad')).toBeRejectedWith(jasmine.any(ScreenshotError));
    } finally {
      restore();
    }
  });
});

describe('analysis history (local only)', () => {
  beforeEach(() => localStorage.removeItem('aia_screenshot_history'));
  afterEach(() => {
    localStorage.removeItem('aia_screenshot_history');
    TestBed.resetTestingModule();
  });

  it('stores text results locally, never image data, and caps the size', () => {
    const h = TestBed.inject(AnalysisHistoryService);
    for (let i = 0; i < MAX_HISTORY + 5; i++) h.add(RESULT, 'Gemini 3.5 Flash Lite', 2);
    expect(h.entries().length).toBe(MAX_HISTORY);
    const raw = localStorage.getItem('aia_screenshot_history')!;
    expect(raw).toContain('JavaScript');
    expect(raw).not.toContain('base64');
  });

  it('removes and clears entries, and tolerates corrupt storage', () => {
    const h = TestBed.inject(AnalysisHistoryService);
    const e = h.add(RESULT, 'm', 1);
    h.remove(e.id);
    expect(h.entries().length).toBe(0);
    h.add(RESULT, 'm', 1);
    h.clear();
    expect(h.entries().length).toBe(0);
    TestBed.resetTestingModule();
    localStorage.setItem('aia_screenshot_history', '{not json');
    expect(TestBed.inject(AnalysisHistoryService).entries()).toEqual([]);
  });
});

describe('splitFences', () => {
  it('separates prose from fenced code', () => {
    expect(splitFences('Use a map.\n```ts\nconst a = 1;\n```\nDone.')).toEqual([
      { kind: 'text', value: 'Use a map.' },
      { kind: 'code', value: 'const a = 1;' },
      { kind: 'text', value: 'Done.' },
    ]);
    expect(splitFences('plain')).toEqual([{ kind: 'text', value: 'plain' }]);
  });
});
