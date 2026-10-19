import { HttpClient, HttpErrorResponse, HttpHeaders, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import {
  DEFAULT_RATE_LIMIT_WAIT_MS,
  MAX_RATE_LIMIT_WAIT_MS,
  rateLimitRetryInterceptor,
  rateLimitWaitMs,
} from './rate-limit-retry.interceptor';

describe('rateLimitRetryInterceptor', () => {
  let http: HttpClient;
  let ctrl: HttpTestingController;
  const tooMany = (retryAfter?: string) => ({
    status: 429,
    statusText: 'Too Many Requests',
    headers: retryAfter ? new HttpHeaders({ 'Retry-After': retryAfter }) : undefined,
  });

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(withInterceptors([rateLimitRetryInterceptor])), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpClient);
    ctrl = TestBed.inject(HttpTestingController);
  });
  afterEach(() => ctrl.verify());

  it('retries a rate-limited GET once after Retry-After and returns the data', fakeAsync(() => {
    let body: unknown;
    http.get('/api/admin/stats').subscribe((b) => (body = b));
    ctrl.expectOne('/api/admin/stats').flush({ detail: 'slow down' }, tooMany('2'));
    tick(1999);
    ctrl.expectNone('/api/admin/stats');
    tick(1);
    ctrl.expectOne('/api/admin/stats').flush({ ok: true });
    expect(body).toEqual({ ok: true });
  }));

  it('gives up after one retry and surfaces the 429', fakeAsync(() => {
    let status = 0;
    http.get('/api/admin/users').subscribe({ error: (e: HttpErrorResponse) => (status = e.status) });
    ctrl.expectOne('/api/admin/users').flush({}, tooMany('1'));
    tick(1000);
    ctrl.expectOne('/api/admin/users').flush({}, tooMany('1'));
    tick(5000);
    ctrl.expectNone('/api/admin/users');
    expect(status).toBe(429);
  }));

  it('never retries non-GET requests (no duplicate actions)', fakeAsync(() => {
    let status = 0;
    http.patch('/api/admin/users/1', {}).subscribe({ error: (e: HttpErrorResponse) => (status = e.status) });
    ctrl.expectOne('/api/admin/users/1').flush({}, tooMany('1'));
    tick(5000);
    ctrl.expectNone('/api/admin/users/1');
    expect(status).toBe(429);
  }));

  it('does not retry other errors', fakeAsync(() => {
    let status = 0;
    http.get('/api/admin/stats').subscribe({ error: (e: HttpErrorResponse) => (status = e.status) });
    ctrl.expectOne('/api/admin/stats').flush({}, { status: 500, statusText: 'Server Error' });
    tick(5000);
    ctrl.expectNone('/api/admin/stats');
    expect(status).toBe(500);
  }));

  it('does not hide long waits: a Retry-After over the cap fails right away', fakeAsync(() => {
    let status = 0;
    http.get('/api/admin/stats').subscribe({ error: (e: HttpErrorResponse) => (status = e.status) });
    ctrl.expectOne('/api/admin/stats').flush({}, tooMany(String(MAX_RATE_LIMIT_WAIT_MS / 1000 + 50)));
    expect(status).toBe(429);
  }));

  it('computes the wait from Retry-After with a sane default', () => {
    const err = (h?: string) => new HttpErrorResponse({ status: 429, headers: h ? new HttpHeaders({ 'Retry-After': h }) : undefined });
    expect(rateLimitWaitMs(err('3'))).toBe(3000);
    expect(rateLimitWaitMs(err())).toBe(DEFAULT_RATE_LIMIT_WAIT_MS);
    expect(rateLimitWaitMs(err('soon'))).toBe(DEFAULT_RATE_LIMIT_WAIT_MS);
    expect(rateLimitWaitMs(err('60'))).toBeNull();
  });
});
