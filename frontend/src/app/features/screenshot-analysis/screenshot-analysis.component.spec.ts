import { ComponentFixture, TestBed } from '@angular/core/testing';
import {
  RESULT,
  b64,
  installDesktopBridge,
  shot,
} from '../../core/screenshot/screenshot.spec';
import { ScreenshotAnalysisComponent } from './screenshot-analysis.component';

describe('ScreenshotAnalysisComponent (desktop flow)', () => {
  let restore: () => void;
  let fixture: ComponentFixture<ScreenshotAnalysisComponent>;
  let cmp: ScreenshotAnalysisComponent;
  const el = () => fixture.nativeElement as HTMLElement;
  const q = (sel: string) => el().querySelector(sel) as HTMLElement | null;
  const settle = async () => {
    await fixture.whenStable();
    fixture.detectChanges();
  };

  async function setup(overrides: Parameters<typeof installDesktopBridge>[0] = {}) {
    restore = installDesktopBridge(overrides);
    fixture = TestBed.createComponent(ScreenshotAnalysisComponent);
    cmp = fixture.componentInstance;
    fixture.detectChanges();
    await settle();
  }

  beforeEach(() => {
    localStorage.removeItem('aia_screenshot_history');
    localStorage.removeItem('aia_screenshot_model');
  });
  afterEach(() => {
    restore?.();
    localStorage.removeItem('aia_screenshot_history');
    TestBed.resetTestingModule();
  });

  it('shows the active default model and the responsible-use note', async () => {
    await setup();
    expect(q('[data-testid="active-model"]')!.textContent).toContain('Gemini 3.5 Flash Lite');
    expect(el().textContent).toContain('misrepresent your work');
    expect(el().textContent).toContain('bypass the rules');
  });

  it('capture flow: button opens the picker, choosing a source captures, thumbnail appears', async () => {
    await setup();
    expect(q('[data-testid="source-picker"]')).toBeNull();
    q('[data-testid="capture-btn"]')!.click();
    await settle();
    expect(q('[data-testid="source-picker"]')).not.toBeNull();
    (q('.source') as HTMLButtonElement).click();
    // Not settle(): whenStable() would also wait out the 3s notice-clearing timer.
    await new Promise((r) => setTimeout(r, 20));
    fixture.detectChanges();
    expect(q('[data-testid="source-picker"]')).toBeNull();
    expect(cmp.shots().length).toBe(1);
    expect(el().querySelectorAll('.thumb').length).toBe(1);
    expect(el().textContent).toContain('Captured');
  });

  it('nothing is captured until the user picks a source', async () => {
    let captures = 0;
    await setup({ capture: async () => (captures++, { ok: true, image: shot() }) });
    q('[data-testid="capture-btn"]')!.click();
    await settle();
    expect(captures).toBe(0);
    cmp.closePicker();
    await settle();
    expect(captures).toBe(0);
  });

  it('supports multiple screenshots with reorder, replace and remove', async () => {
    let n = 0;
    await setup({ capture: async () => ({ ok: true, image: shot(100, { width: 800 + n++ }) }) });
    for (let i = 0; i < 3; i++) {
      await cmp.openPicker({ kind: 'add' });
      await cmp.takeCapture(cmp.sources()[0]);
    }
    expect(cmp.shots().map((s) => s.image.width)).toEqual([800, 801, 802]);
    cmp.move(2, -1);
    expect(cmp.shots().map((s) => s.image.width)).toEqual([800, 802, 801]);
    cmp.move(0, -1); // no-op at the edge
    expect(cmp.shots().map((s) => s.image.width)).toEqual([800, 802, 801]);
    await cmp.openPicker({ kind: 'replace', index: 0 });
    await cmp.takeCapture(cmp.sources()[0]);
    expect(cmp.shots().length).toBe(3);
    expect(cmp.shots()[0].image.width).toBe(803);
    cmp.remove(1);
    expect(cmp.shots().length).toBe(2);
  });

  it('blocks analysis and explains why when validation fails', async () => {
    await setup({ capture: async () => ({ ok: true, image: shot(100, { mimeType: 'image/gif' }) }) });
    await cmp.openPicker({ kind: 'add' });
    await cmp.takeCapture(cmp.sources()[0]);
    await settle();
    expect(cmp.canAnalyze()).toBeFalse();
    expect((q('[data-testid="analyze-btn"]') as HTMLButtonElement).disabled).toBeTrue();
    expect(q('[data-testid="validation-errors"]')!.textContent).toContain('unsupported image type');
  });

  it('stops adding at the model image limit', async () => {
    await setup();
    const max = cmp.config.activeModel().limits.maxImages;
    for (let i = 0; i < max; i++) {
      await cmp.openPicker({ kind: 'add' });
      await cmp.takeCapture(cmp.sources()[0]);
    }
    await settle();
    expect(cmp.atLimit()).toBeTrue();
    expect((q('[data-testid="capture-btn"]') as HTMLButtonElement).disabled).toBeTrue();
  });

  it('analyzes, renders the result, and saves it to local history only', async () => {
    await setup();
    await cmp.openPicker({ kind: 'add' });
    await cmp.takeCapture(cmp.sources()[0]);
    await cmp.analyze();
    await settle();
    expect(cmp.result()).toEqual(RESULT);
    expect(el().textContent).toContain('Direct answer');
    expect(el().textContent).toContain('Interview-style answer');
    expect(el().textContent).toContain('Likely follow-ups');
    expect(cmp.history.entries().length).toBe(1);
    expect(localStorage.getItem('aia_screenshot_history')).not.toContain(b64(100));
  });

  it('shows a clear error and recovers when analysis fails', async () => {
    await setup({ analyze: async () => ({ ok: false, error: { code: 'INVALID_KEY', message: 'The API key was rejected.' } }) });
    await cmp.openPicker({ kind: 'add' });
    await cmp.takeCapture(cmp.sources()[0]);
    await cmp.analyze();
    await settle();
    expect(cmp.error()).toBe('The API key was rejected.');
    expect(cmp.analyzing()).toBeFalse();
    expect(cmp.result()).toBeNull();
    expect(cmp.history.entries().length).toBe(0);
  });

  it('shows a clear error when capture or source listing fails', async () => {
    await setup({ listSources: async () => ({ ok: false, error: { code: 'X', message: 'Screen capture was blocked.' } }) });
    await cmp.openPicker({ kind: 'add' });
    expect(cmp.error()).toBe('Screen capture was blocked.');
    expect(cmp.picker()).toBeNull();
  });

  it('keeps the picker open and reports the error when a capture fails', async () => {
    await setup({ capture: async () => ({ ok: false, error: { code: 'EMPTY_CAPTURE', message: 'The capture came back empty.' } }) });
    await cmp.openPicker({ kind: 'add' });
    await cmp.takeCapture(cmp.sources()[0]);
    expect(cmp.error()).toBe('The capture came back empty.');
    expect(cmp.shots().length).toBe(0);
  });

  it('requires an API key before analysis and reports key-save errors', async () => {
    await setup({
      getConfig: async () => ({ ok: true, models: cmp ? cmp.config.models() : [], defaultModel: 'gemini-3.5-flash-lite', keyStored: {}, encryptionAvailable: true }),
      setKey: async () => ({ ok: false, error: { code: 'ENCRYPTION_UNAVAILABLE', message: 'Secure storage is not available.' } }),
    });
    await cmp.openPicker({ kind: 'add' });
    await cmp.takeCapture(cmp.sources()[0]);
    await settle();
    expect(cmp.config.hasKey()).toBeFalse();
    expect(cmp.canAnalyze()).toBeFalse();
    cmp.keyInput = 'AIzaSECRET_KEY_123456';
    await cmp.saveKey();
    expect(cmp.keyError()).toBe('Secure storage is not available.');
    expect(cmp.config.hasKey()).toBeFalse();
  });

  it('shows that the key was loaded automatically from .env, with no key input', async () => {
    await setup({
      getConfig: async () => ({ ok: true, models: cmp.config.models(), defaultModel: 'gemini-3.5-flash-lite', keyStored: { google: true }, keySources: { google: 'env' }, envHint: '/data/.env', encryptionAvailable: true }),
    });
    expect(q('[data-testid="key-status"]')!.textContent).toContain('loaded automatically from .env');
    expect(q('#apikey')).toBeNull();
    expect(cmp.config.hasKey()).toBeTrue();
  });
});
