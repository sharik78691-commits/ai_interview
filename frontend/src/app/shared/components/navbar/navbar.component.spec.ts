import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { BehaviorSubject, of } from 'rxjs';
import { AuthService } from '../../../core/auth/auth.service';
import { ResumeService } from '../../../core/services/resume.service';
import { SettingsService } from '../../../core/services/settings.service';
import { NavbarComponent } from './navbar.component';

describe('NavbarComponent', () => {
  beforeEach(() =>
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: ResumeService, useValue: { checkHealth: () => of({ llm_configured: true }) } },
        { provide: SettingsService, useValue: { theme: 'light', settings$: of({ theme: 'light' }), toggleTheme: () => 'dark' } },
        { provide: AuthService, useValue: { user$: new BehaviorSubject(null), logout: () => of(null) } },
      ],
    }),
  );

  // Regression: the desktop navbar advertised "Auto invisible on screen share".
  it('desktop navbar does not show the screen-share invisibility badge', () => {
    const f = TestBed.createComponent(NavbarComponent);
    (f.componentInstance as { isDesktop: boolean }).isDesktop = true;
    f.detectChanges();
    const el = f.nativeElement as HTMLElement;
    expect(el.querySelector('.invis-flash')).toBeNull();
    expect(el.textContent).not.toContain('invisible on screen share');
    expect(el.querySelector('.hotkey-hint')).not.toBeNull(); // other desktop UI unchanged
  });

  it('labels real AI mode as "AI", not "LIVE AI"', () => {
    const f = TestBed.createComponent(NavbarComponent);
    f.detectChanges();
    const badge = (f.nativeElement as HTMLElement).querySelector('.live-badge');
    expect(badge?.textContent?.trim()).toBe('AI');
    expect((f.nativeElement as HTMLElement).textContent).not.toContain('LIVE AI');
  });
});
