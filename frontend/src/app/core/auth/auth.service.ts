import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, NgZone, OnDestroy, inject } from '@angular/core';
import {
  BehaviorSubject,
  Observable,
  Subject,
  catchError,
  finalize,
  map,
  of,
  share,
  tap,
  throwError,
} from 'rxjs';
import { AuthStatus, AuthUser, LoginPayload, RegisterPayload } from '../models/auth.models';
import { environment } from '../../../environments/environment';

/**
 * Base URL for the API.
 *
 * In development the Angular dev-server proxies `/api` to the backend, so a
 * relative base keeps the session cookie same-origin. In production the app is
 * served from the same origin as the API (or a reverse proxy), so relative
 * paths keep working without hard-coded hosts. In the Electron build it is an
 * absolute URL (no proxy exists under `file://`).
 */
const BASE = environment.apiBaseUrl;

/** Lets every open tab of the app learn that the user signed out. */
const LOGOUT_CHANNEL = 'aia-auth';

/**
 * Authentication service.
 *
 * The session lives in a secure, HttpOnly cookie managed by the backend — this
 * service NEVER stores tokens in localStorage/sessionStorage. It only keeps the
 * current user projection in memory for the UI.
 */
@Injectable({ providedIn: 'root' })
export class AuthService implements OnDestroy {
  private http = inject(HttpClient);
  private zone = inject(NgZone);

  private userSubject = new BehaviorSubject<AuthUser | null>(null);
  /** Current authenticated user (null when signed out). */
  user$ = this.userSubject.asObservable();

  /** True once the initial session check has completed. */
  private readySubject = new BehaviorSubject<boolean>(false);
  ready$ = this.readySubject.asObservable();

  /** Emits after a sign-out in this tab or another tab, so app state can be cleared. */
  private loggedOutSubject = new Subject<void>();
  loggedOut$ = this.loggedOutSubject.asObservable();

  private logoutRequest: Observable<void> | null = null;
  private channel: BroadcastChannel | null =
    typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(LOGOUT_CHANNEL);

  constructor() {
    if (this.channel) {
      this.channel.onmessage = (ev: MessageEvent) => {
        if (ev.data === 'logout') this.zone.run(() => this.endLocalSession());
      };
    }
  }

  ngOnDestroy(): void {
    this.channel?.close();
  }

  get currentUser(): AuthUser | null {
    return this.userSubject.value;
  }

  get isAuthenticated(): boolean {
    return this.userSubject.value !== null;
  }

  /** Read the readable CSRF cookie for the double-submit header. */
  private csrfToken(): string {
    const match = document.cookie.match(/(?:^|;\s*)aia_csrf=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  private csrfHeaders(): Record<string, string> {
    const token = this.csrfToken();
    return token ? { 'X-CSRF-Token': token } : {};
  }

  /** Fetch the current session from the backend. */
  checkSession(): Observable<AuthUser | null> {
    return this.http.get<AuthStatus>(`${BASE}/api/auth/me`).pipe(
      map((s) => (s.authenticated ? s.user : null)),
      tap((user) => {
        this.userSubject.next(user);
        this.readySubject.next(true);
      }),
      catchError(() => {
        this.userSubject.next(null);
        this.readySubject.next(true);
        return of(null);
      }),
    );
  }

  register(payload: RegisterPayload): Observable<AuthUser> {
    return this.http
      .post<AuthStatus>(`${BASE}/api/auth/register`, payload)
      .pipe(
        map((s) => s.user as AuthUser),
        tap((user) => this.userSubject.next(user)),
      );
  }

  login(payload: LoginPayload): Observable<AuthUser> {
    return this.http.post<AuthStatus>(`${BASE}/api/auth/login`, payload).pipe(
      map((s) => s.user as AuthUser),
      tap((user) => this.userSubject.next(user)),
    );
  }

  /**
   * Sign out on the server, then clear local state in every open tab.
   * Repeated clicks share the request already in flight.
   */
  logout(): Observable<void> {
    if (this.logoutRequest) return this.logoutRequest;
    this.logoutRequest = this.http
      .post<{ message: string }>(`${BASE}/api/auth/logout`, {}, { headers: this.csrfHeaders() })
      .pipe(
        map(() => void 0),
        tap(() => this.finishLogout()),
        catchError((err) => {
          // Even if the call fails, do not leave the previous user's data on screen.
          this.finishLogout();
          return throwError(() => err);
        }),
        finalize(() => (this.logoutRequest = null)),
        share(),
      );
    return this.logoutRequest;
  }

  private finishLogout(): void {
    this.channel?.postMessage('logout');
    this.endLocalSession();
  }

  private endLocalSession(): void {
    this.userSubject.next(null);
    this.loggedOutSubject.next();
  }

  forgotPassword(email: string): Observable<{ message: string }> {
    return this.http.post<{ message: string }>(`${BASE}/api/auth/forgot-password`, { email });
  }

  resetPassword(token: string, password: string): Observable<{ message: string }> {
    return this.http.post<{ message: string }>(`${BASE}/api/auth/reset-password`, {
      token,
      password,
    });
  }

  /** Full-page redirect to the backend Google OAuth entrypoint. */
  googleLoginUrl(): string {
    return `${BASE}/api/auth/google/login`;
  }

  /** Map a backend error into a user-friendly message. */
  static friendlyError(err: unknown): string {
    const httpErr = err as HttpErrorResponse;
    const detail = (httpErr?.error?.detail as string) || '';
    if (httpErr?.status === 0) {
      return 'Unable to reach the server. Please try again.';
    }
    if (httpErr?.status === 429) {
      return 'Too many login attempts. Please try again later.';
    }
    if (httpErr?.status === 401) {
      return detail || 'Invalid email or password.';
    }
    if (httpErr?.status === 409) {
      return detail || 'Account already exists.';
    }
    if (httpErr?.status === 400) {
      return detail || 'Password reset link is invalid or expired.';
    }
    if (httpErr?.status === 422) {
      return detail || 'Please check the details you entered.';
    }
    return detail || 'Something went wrong. Please try again.';
  }
}
