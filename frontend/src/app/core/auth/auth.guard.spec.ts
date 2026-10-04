import { TestBed } from '@angular/core/testing';
import { Router, UrlTree } from '@angular/router';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { AuthService } from './auth.service';
import { authGuard, guestGuard } from './auth.guard';

describe('authGuard', () => {
  let auth: jasmine.SpyObj<AuthService>;

  beforeEach(() => {
    auth = jasmine.createSpyObj<AuthService>('AuthService', ['checkSession'], {
      isAuthenticated: false,
    });
    TestBed.configureTestingModule({
      providers: [provideRouter([]), { provide: AuthService, useValue: auth }],
    });
  });

  it('allows access when already authenticated', () => {
    Object.defineProperty(auth, 'isAuthenticated', { get: () => true });
    const result = TestBed.runInInjectionContext(() =>
      authGuard({} as never, { url: '/dashboard' } as never),
    );
    expect(result).toBeTrue();
  });

  it('redirects to /login when the session check fails', (done) => {
    auth.checkSession.and.returnValue(of(null));
    const result = TestBed.runInInjectionContext(() =>
      authGuard({} as never, { url: '/dashboard' } as never),
    ) as ReturnType<typeof of>;
    (result as never as import('rxjs').Observable<boolean | UrlTree>).subscribe((r) => {
      expect(r instanceof UrlTree).toBeTrue();
      expect((r as UrlTree).toString()).toContain('/login');
      done();
    });
  });

  it('allows access when the session check succeeds', (done) => {
    auth.checkSession.and.returnValue(
      of({ id: '1', email: 'a@b.com', name: 'A', provider: 'local' }),
    );
    const result = TestBed.runInInjectionContext(() =>
      authGuard({} as never, { url: '/dashboard' } as never),
    ) as never as import('rxjs').Observable<boolean | UrlTree>;
    result.subscribe((r) => {
      expect(r).toBeTrue();
      done();
    });
  });
});

describe('guestGuard', () => {
  let auth: jasmine.SpyObj<AuthService>;

  beforeEach(() => {
    auth = jasmine.createSpyObj<AuthService>('AuthService', ['checkSession'], {
      isAuthenticated: false,
    });
    TestBed.configureTestingModule({
      providers: [provideRouter([]), { provide: AuthService, useValue: auth }],
    });
  });

  it('redirects authenticated users to /dashboard', () => {
    Object.defineProperty(auth, 'isAuthenticated', { get: () => true });
    const result = TestBed.runInInjectionContext(() => guestGuard({} as never, {} as never));
    expect(result instanceof UrlTree).toBeTrue();
    expect((result as UrlTree).toString()).toContain('/dashboard');
  });

  it('allows guests through', (done) => {
    auth.checkSession.and.returnValue(of(null));
    const result = TestBed.runInInjectionContext(() =>
      guestGuard({} as never, {} as never),
    ) as never as import('rxjs').Observable<boolean | UrlTree>;
    result.subscribe((r) => {
      expect(r).toBeTrue();
      done();
    });
  });
});
