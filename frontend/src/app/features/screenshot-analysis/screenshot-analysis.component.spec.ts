import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import {
  RESULT,
  b64,
  installDesktopBridge,
  shot,
} from '../../core/screenshot/screenshot.spec';
import { ScreenCaptureService } from '../../core/screenshot/screen-capture.service';
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

  it('shows the active default model, without the removed responsible-use banner', async () => {
    await setup();
    expect(q('[data-testid="active-model"]')!.textContent).toContain('Gemini 3.5 Flash Lite');
    expect(q('.notice[role="note"]')).toBeNull();
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
    await setup({ analyze: async () => ({ ok: false, error: { code: 'PROVIDER_ERROR', message: 'The AI provider is having trouble. Try again shortly.' } }) });
    await cmp.openPicker({ kind: 'add' });
    await cmp.takeCapture(cmp.sources()[0]);
    await cmp.analyze();
    await settle();
    expect(cmp.error()).toBe('The AI provider is having trouble. Try again shortly.');
    expect(cmp.analyzing()).toBeFalse();
    expect(cmp.result()).toBeNull();
    expect(cmp.history.entries().length).toBe(0);
  });

  const USAGE = { planName: 'Weekly', allowance: 40, used: 3, remaining: 37, totalRemaining: 37, expiresAt: null };

  it('shows remaining screenshot credits from config and updates them after an analysis', async () => {
    await setup({
      getConfig: async () => ({ ok: true, models: cmp.config.models(), defaultModel: 'gemini-3.5-flash-lite', serverReady: true, usage: USAGE }),
      analyze: async () => ({ ok: true, result: RESULT, model: 'Gemini 3.5 Flash Lite', usage: { ...USAGE, used: 4, remaining: 36, totalRemaining: 36 } }),
    });
    expect(q('[data-testid="credits-remaining"]')!.textContent).toContain('37 screenshot credits left on Weekly');
    await cmp.openPicker({ kind: 'add' });
    await cmp.takeCapture(cmp.sources()[0]);
    await cmp.analyze();
    await settle();
    expect(q('[data-testid="credits-remaining"]')!.textContent).toContain('36 screenshot credits left');
  });

  it('hides the credits line when the server does not report usage', async () => {
    await setup();
    expect(q('[data-testid="credits-remaining"]')).toBeNull();
  });

  for (const code of ['NO_CREDITS', 'SUBSCRIPTION_REQUIRED']) {
    it(`${code}: shows the server message and a link to Pricing`, async () => {
      TestBed.configureTestingModule({ providers: [provideRouter([])] });
      await setup({ analyze: async () => ({ ok: false, error: { code, message: 'Buy a pass on the Pricing page.' } }) });
      await cmp.openPicker({ kind: 'add' });
      await cmp.takeCapture(cmp.sources()[0]);
      await cmp.analyze();
      await settle();
      expect(cmp.error()).toBe('Buy a pass on the Pricing page.');
      const link = q('[data-testid="plan-cta"] a') as HTMLAnchorElement;
      expect(link.getAttribute('href')).toContain('/pricing');
    });
  }

  it('does not offer Pricing for other errors', async () => {
    await setup({ analyze: async () => ({ ok: false, error: { code: 'RATE_LIMITED', message: 'Slow down.' } }) });
    await cmp.openPicker({ kind: 'add' });
    await cmp.takeCapture(cmp.sources()[0]);
    await cmp.analyze();
    await settle();
    expect(q('[data-testid="plan-cta"]')).toBeNull();
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

  it('has no API key input: analysis runs on the server', async () => {
    await setup();
    expect(q('#apikey')).toBeNull();
    expect(el().textContent).not.toContain('Paste your API key');
    // AI Model section removed from the page; no model picker, no status card.
    expect(q('[data-testid="model-settings"]')).toBeNull();
    expect(q('#model')).toBeNull();
    expect(q('[data-testid="server-status"]')).toBeNull();
    await cmp.openPicker({ kind: 'add' });
    await cmp.takeCapture(cmp.sources()[0]);
    await settle();
    expect(cmp.canAnalyze()).toBeTrue();
  });

  it('blocks analysis with a clear note when the server is not set up', async () => {
    await setup({
      getConfig: async () => ({ ok: true, models: cmp.config.models(), defaultModel: 'gemini-3.5-flash-lite', serverReady: false }),
    });
    await cmp.openPicker({ kind: 'add' });
    await cmp.takeCapture(cmp.sources()[0]);
    await settle();
    expect(cmp.canAnalyze()).toBeFalse();
    expect(q('[data-testid="server-status"]')!.textContent).toContain('not set up on the server');
  });
  it('Ctrl+Shift+S hotkey: picker mode only while the page is open; opens the picker, arrows choose, Enter captures and analyzes', async () => {
    const calls: boolean[] = [];
    let press: ((id: string | null, err: { code: string; message: string } | null) => void) | null = null;
    const captured: string[] = [];
    let analyzedCount = 0;
    const sources = [
      { id: 'screen:1:0', name: 'Screen 1', kind: 'screen', thumbnail: 'data:image/png;base64,AAAA' },
      { id: 'screen:2:0', name: 'Screen 2', kind: 'screen', thumbnail: 'data:image/png;base64,AAAA' },
      { id: 'window:9:0', name: 'Editor', kind: 'window', thumbnail: 'data:image/png;base64,AAAA' },
    ];
    await setup({
      setHotkey: async (on: boolean) => {
        calls.push(on);
        return { ok: true, active: on };
      },
      onHotkey: (cb) => {
        press = cb;
        return () => (press = null);
      },
      listSources: async () => ({ ok: true, sources }),
      capture: async (id: string) => {
        captured.push(id);
        return { ok: true, image: shot() };
      },
      analyze: async (_m: string, images: unknown[]) => {
        analyzedCount = images.length;
        return { ok: true, result: RESULT, model: 'Gemini 3.5 Flash Lite' };
      },
    } as Parameters<typeof installDesktopBridge>[0]);
    expect(calls).toEqual([true]);
    expect(q('[data-testid="capture-hotkey-tip"]')!.textContent).toContain('Shift');
    expect(q('[data-testid="title-hotkey-hint"]')!.textContent!.replace(/\s/g, '')).toBe('Ctrl+Shift+S');
    expect(q('[data-testid="title-hotkey-steps"]')!.textContent).toContain('Enter');

    // Hotkey opens the picker with the screen under the cursor preselected — nothing captured yet.
    press!('screen:2:0', null);
    await new Promise((r) => setTimeout(r, 20));
    fixture.detectChanges();
    const modal = q('[data-testid="source-picker"]')!;
    expect(modal).not.toBeNull();
    expect(captured).toEqual([]);
    expect(cmp.selectedIndex()).toBe(1);
    expect(el().querySelectorAll('.source.selected').length).toBe(1);

    const key = (k: string) => modal.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }));
    key('ArrowRight');
    expect(cmp.selectedIndex()).toBe(2);
    key('ArrowRight');
    expect(cmp.selectedIndex()).toBe(0); // wraps
    key('ArrowLeft');
    expect(cmp.selectedIndex()).toBe(2);
    // Up/Down move by grid row, falling back to the next/previous item at the edges.
    key('ArrowDown');
    expect(cmp.selectedIndex()).toBe(0);
    key('ArrowUp');
    expect(cmp.selectedIndex()).toBe(2);
    key('ArrowLeft');
    expect(cmp.selectedIndex()).toBe(1);
    key('Enter');
    await new Promise((r) => setTimeout(r, 20));
    fixture.detectChanges();
    expect(captured).toEqual(['screen:2:0']);
    expect(cmp.shots().length).toBe(1);
    expect(q('[data-testid="source-picker"]')).toBeNull();
    // Enter = capture + analyze immediately, no click on "Analyze".
    expect(analyzedCount).toBe(1);
    expect(cmp.result()).toEqual(RESULT);

    // Escape cancels without capturing.
    press!('screen:1:0', null);
    await new Promise((r) => setTimeout(r, 20));
    fixture.detectChanges();
    q('[data-testid="source-picker"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    fixture.detectChanges();
    expect(q('[data-testid="source-picker"]')).toBeNull();
    expect(captured).toEqual(['screen:2:0']);

    fixture.destroy();
    expect(calls).toEqual([true, false]);
    expect(press).toBeNull();
  });

  it('clicking a source only captures (no automatic analysis), so several can be collected', async () => {
    let analyzeCalls = 0;
    await setup({
      analyze: async () => {
        analyzeCalls++;
        return { ok: true, result: RESULT, model: 'Gemini 3.5 Flash Lite' };
      },
    });
    await cmp.openPicker({ kind: 'add' });
    await cmp.takeCapture(cmp.sources()[0]);
    expect(cmp.shots().length).toBe(1);
    expect(analyzeCalls).toBe(0);
  });

  it('Enter does not analyze when the capture fails', async () => {
    let analyzeCalls = 0;
    await setup({
      capture: async () => ({ ok: false, error: { code: 'EMPTY_CAPTURE', message: 'The capture came back empty.' } }),
      analyze: async () => {
        analyzeCalls++;
        return { ok: true, result: RESULT, model: 'Gemini 3.5 Flash Lite' };
      },
    });
    await cmp.openPicker({ kind: 'add' });
    await cmp.takeCapture(cmp.sources()[0], { analyzeNow: true });
    expect(analyzeCalls).toBe(0);
    expect(cmp.error()).toBe('The capture came back empty.');
  });

  it('Ctrl+Shift+S hotkey reports errors instead of capturing', async () => {
    let press: ((id: string | null, err: { code: string; message: string } | null) => void) | null = null;
    await setup({
      setHotkey: async (on: boolean) => ({ ok: true, active: on }),
      onHotkey: (cb) => {
        press = cb;
        return () => (press = null);
      },
    });
    await cmp.onHotkey(null, null);
    expect(cmp.error()).toContain('Could not find a screen');
    expect(cmp.shots().length).toBe(0);
    press!(null, { code: 'SOURCE_GONE', message: 'No screen available to capture.' });
    await new Promise((r) => setTimeout(r, 20));
    expect(cmp.shots().length).toBe(0);
  });
});

describe('ScreenshotAnalysisComponent (Ctrl+Shift+S from another page)', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('one press opens the picker on the cursor screen; Enter captures and analyzes', async () => {
    const captured: string[] = [];
    let analyzed = 0;
    const sources = [
      { id: 'screen:1:0', name: 'Screen 1', kind: 'screen', thumbnail: 'data:image/png;base64,AAAA' },
      { id: 'screen:2:0', name: 'Screen 2', kind: 'screen', thumbnail: 'data:image/png;base64,AAAA' },
    ];
    let openPage: ((id: string | null) => void) | null = null;
    const restore = installDesktopBridge({
      onOpenPage: (cb: (id: string | null) => void) => {
        openPage = cb;
        return () => (openPage = null);
      },
      listSources: async () => ({ ok: true, sources }),
      capture: async (id: string) => {
        captured.push(id);
        return { ok: true, image: shot() };
      },
      analyze: async () => {
        analyzed++;
        return { ok: true, result: RESULT, model: 'Gemini 3.5 Flash Lite' };
      },
    } as Parameters<typeof installDesktopBridge>[0]);
    try {
      // App shell: the press arrives on another page and the app navigates here.
      TestBed.inject(ScreenCaptureService).onOpenPageRequest(() => {});
      openPage!('screen:2:0');

      const fixture = TestBed.createComponent(ScreenshotAnalysisComponent);
      const cmp = fixture.componentInstance;
      fixture.detectChanges();
      await new Promise((r) => setTimeout(r, 20));
      fixture.detectChanges();
      const modal = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="source-picker"]')!;
      expect(modal).not.toBeNull();
      expect(cmp.selectedIndex()).toBe(1);
      expect(captured).toEqual([]);

      modal.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      await new Promise((r) => setTimeout(r, 20));
      expect(captured).toEqual(['screen:2:0']);
      expect(analyzed).toBe(1);
      expect(cmp.result()).not.toBeNull();
    } finally {
      restore();
    }
  });
});
