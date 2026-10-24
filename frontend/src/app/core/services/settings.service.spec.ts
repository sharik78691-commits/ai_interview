import { environment } from '../../../environments/environment';
import { SettingsService } from './settings.service';

describe('SettingsService theme default', () => {
  beforeEach(() => localStorage.removeItem('aia_settings'));
  afterEach(() => {
    localStorage.removeItem('aia_settings');
    document.documentElement.removeAttribute('data-theme');
  });

  it('defaults to light mode for new visitors', () => {
    const svc = new SettingsService();
    expect(svc.theme).toBe('light');
    expect(document.documentElement.dataset['theme']).toBe('light');
  });

  it('keeps an explicitly saved dark choice', () => {
    localStorage.setItem('aia_settings', JSON.stringify({ theme: 'dark' }));
    const svc = new SettingsService();
    expect(svc.theme).toBe('dark');
    expect(document.documentElement.dataset['theme']).toBe('dark');
  });

  describe('desktop (.exe) build', () => {
    const prev = environment.electron;
    beforeEach(() => ((environment as { electron: boolean }).electron = true));
    afterEach(() => ((environment as { electron: boolean }).electron = prev));

    it('defaults to dark mode for a fresh install', () => {
      const svc = new SettingsService();
      expect(svc.theme).toBe('dark');
      expect(document.documentElement.dataset['theme']).toBe('dark');
    });

    it('keeps an explicitly saved light choice', () => {
      localStorage.setItem('aia_settings', JSON.stringify({ theme: 'light' }));
      const svc = new SettingsService();
      expect(svc.theme).toBe('light');
    });

    it('reset goes back to dark', () => {
      localStorage.setItem('aia_settings', JSON.stringify({ theme: 'light' }));
      const svc = new SettingsService();
      svc.reset();
      expect(svc.theme).toBe('dark');
      expect(document.documentElement.dataset['theme']).toBe('dark');
    });
  });
});

describe('SettingsService background color', () => {
  beforeEach(() => localStorage.removeItem('aia_settings'));
  afterEach(() => {
    localStorage.removeItem('aia_settings');
    document.documentElement.removeAttribute('data-theme');
    document.documentElement.removeAttribute('data-bg');
  });

  it('uses the sunshine yellow background for new visitors', () => {
    const svc = new SettingsService();
    expect(svc.value.background).toBe('sunshine');
    expect(document.documentElement.dataset['bg']).toBe('sunshine');
  });

  it('keeps an explicitly saved original-green background', () => {
    localStorage.setItem('aia_settings', JSON.stringify({ background: 'default', bgVersion: 2 }));
    const svc = new SettingsService();
    expect(svc.value.background).toBe('default');
    expect(document.documentElement.hasAttribute('data-bg')).toBeFalse();
  });

  it('moves a background saved before the yellow default to yellow once', () => {
    localStorage.setItem('aia_settings', JSON.stringify({ background: 'calm' }));
    const svc = new SettingsService();
    expect(svc.value.background).toBe('sunshine');
    expect(document.documentElement.dataset['bg']).toBe('sunshine');
  });

  it('applies and persists a picked background', () => {
    const svc = new SettingsService();
    svc.save({ background: 'blue' });
    expect(document.documentElement.dataset['bg']).toBe('blue');
    expect(new SettingsService().value.background).toBe('blue');
  });

  it('falls back to sunshine yellow for an unknown stored value', () => {
    localStorage.setItem('aia_settings', JSON.stringify({ background: 'neon', bgVersion: 2 }));
    const svc = new SettingsService();
    expect(svc.value.background).toBe('sunshine');
    expect(document.documentElement.dataset['bg']).toBe('sunshine');
  });

  it('keeps the background when the theme is toggled, and reset clears it', () => {
    const svc = new SettingsService();
    svc.save({ background: 'purple' });
    svc.toggleTheme();
    expect(document.documentElement.dataset['bg']).toBe('purple');
    svc.reset();
    expect(svc.value.background).toBe('sunshine');
    expect(document.documentElement.dataset['bg']).toBe('sunshine');
  });
});
