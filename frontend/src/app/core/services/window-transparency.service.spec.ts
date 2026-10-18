import { TestBed } from '@angular/core/testing';
import { environment } from '../../../environments/environment';
import { MAX_TRANSPARENCY, TRANSPARENCY_KEY, WindowTransparencyService } from './window-transparency.service';

describe('WindowTransparencyService (global desktop slider)', () => {
  let opacities: number[];
  const prevFlag = environment.electron;
  const prevApi = window.electronAPI;

  beforeEach(() => {
    opacities = [];
    localStorage.removeItem(TRANSPARENCY_KEY);
    window.electronAPI = {
      isElectron: true,
      setOpacity: async (o: number) => (opacities.push(o), true),
    } as unknown as typeof window.electronAPI;
  });
  afterEach(() => {
    environment.electron = prevFlag;
    window.electronAPI = prevApi;
    localStorage.removeItem(TRANSPARENCY_KEY);
    TestBed.resetTestingModule();
  });

  it('does nothing in the web build', () => {
    environment.electron = false;
    TestBed.inject(WindowTransparencyService);
    expect(opacities).toEqual([]);
  });

  it('restores the saved value on startup (any page) and applies it to the window', () => {
    environment.electron = true;
    localStorage.setItem(TRANSPARENCY_KEY, '40');
    const svc = TestBed.inject(WindowTransparencyService);
    expect(svc.percent()).toBe(40);
    expect(opacities).toEqual([0.6]);
  });

  it('set() clamps to 0–88, persists, and updates opacity', () => {
    environment.electron = true;
    const svc = TestBed.inject(WindowTransparencyService);
    svc.set(50);
    expect(localStorage.getItem(TRANSPARENCY_KEY)).toBe('50');
    svc.set(100);
    expect(svc.percent()).toBe(MAX_TRANSPARENCY);
    svc.set(-5);
    expect(svc.percent()).toBe(0);
    expect(opacities[opacities.length - 1]).toBe(1);
  });

  it('clamps legacy stored values above the maximum', () => {
    environment.electron = true;
    localStorage.setItem(TRANSPARENCY_KEY, '100');
    expect(TestBed.inject(WindowTransparencyService).percent()).toBe(MAX_TRANSPARENCY);
  });
});
