import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { AuthService } from './auth.service';

describe('AuthService', () => {
  let service: AuthService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(AuthService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it('starts unauthenticated', () => {
    expect(service.isAuthenticated).toBeFalse();
    expect(service.currentUser).toBeNull();
  });

  it('checkSession sets the user when authenticated', () => {
    service.checkSession().subscribe();
    const req = httpMock.expectOne('/api/auth/me');
    req.flush({
      authenticated: true,
      user: { id: '1', email: 'a@b.com', name: 'A', provider: 'local' },
    });
    expect(service.isAuthenticated).toBeTrue();
    expect(service.currentUser?.email).toBe('a@b.com');
  });

  it('checkSession clears the user when unauthenticated', () => {
    service.checkSession().subscribe();
    httpMock.expectOne('/api/auth/me').flush({ authenticated: false, user: null });
    expect(service.isAuthenticated).toBeFalse();
  });

  it('login stores the returned user', () => {
    service.login({ email: 'a@b.com', password: 'Passw0rd123' }).subscribe();
    const req = httpMock.expectOne('/api/auth/login');
    expect(req.request.method).toBe('POST');
    req.flush({
      authenticated: true,
      user: { id: '2', email: 'a@b.com', name: 'A', provider: 'local' },
    });
    expect(service.currentUser?.id).toBe('2');
  });

  it('register stores the returned user', () => {
    service
      .register({ name: 'A', email: 'a@b.com', password: 'Passw0rd123' })
      .subscribe();
    const req = httpMock.expectOne('/api/auth/register');
    expect(req.request.method).toBe('POST');
    req.flush({
      authenticated: true,
      user: { id: '3', email: 'a@b.com', name: 'A', provider: 'local' },
    });
    expect(service.currentUser?.id).toBe('3');
  });

  it('logout clears the user', () => {
    service.login({ email: 'a@b.com', password: 'Passw0rd123' }).subscribe();
    httpMock.expectOne('/api/auth/login').flush({
      authenticated: true,
      user: { id: '4', email: 'a@b.com', name: 'A', provider: 'local' },
    });
    expect(service.isAuthenticated).toBeTrue();

    service.logout().subscribe();
    httpMock.expectOne('/api/auth/logout').flush({ message: 'Signed out.' });
    expect(service.isAuthenticated).toBeFalse();
  });

  it('maps errors to friendly messages', () => {
    expect(AuthService.friendlyError({ status: 401, error: { detail: 'Invalid email or password.' } })).toBe(
      'Invalid email or password.',
    );
    expect(AuthService.friendlyError({ status: 429, error: {} })).toBe(
      'Too many login attempts. Please try again later.',
    );
    expect(AuthService.friendlyError({ status: 0, error: {} })).toBe(
      'Unable to reach the server. Please try again.',
    );
  });

  it('exposes the Google login URL', () => {
    expect(service.googleLoginUrl()).toBe('/api/auth/google/login');
  });
});
