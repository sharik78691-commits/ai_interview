import { HttpRequest, HttpResponse } from '@angular/common/http';
import { PrerenderHttpBackend } from './prerender-http-backend';

describe('PrerenderHttpBackend', () => {
  const backend = new PrerenderHttpBackend();

  it('answers the session check as signed out', (done) => {
    backend.handle(new HttpRequest('GET', '/api/auth/me')).subscribe((ev) => {
      expect(ev instanceof HttpResponse).toBeTrue();
      expect((ev as HttpResponse<{ authenticated: boolean }>).body?.authenticated).toBeFalse();
      done();
    });
  });

  it('completes every other request without a response or an error', () => {
    const events: unknown[] = [];
    let completed = false;
    let failed = false;
    backend.handle(new HttpRequest('GET', '/api/plans?currency=INR')).subscribe({
      next: (ev) => events.push(ev),
      error: () => (failed = true),
      complete: () => (completed = true),
    });
    expect(events).toEqual([]);
    expect(failed).toBeFalse();
    expect(completed).toBeTrue();
  });
});
