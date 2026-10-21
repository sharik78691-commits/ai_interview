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
