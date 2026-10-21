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
});
