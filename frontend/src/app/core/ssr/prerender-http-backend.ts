import { Injectable } from '@angular/core';
import { HttpBackend, HttpEvent, HttpRequest, HttpResponse } from '@angular/common/http';
import { EMPTY, Observable, of } from 'rxjs';

/**
 * HTTP backend used ONLY while prerendering (app.config.server.ts). The build
 * machine has no API, so nothing goes over the network:
 * - `/api/auth/me` answers "signed out", so guest-only pages (login, register)
 *   render and the navbar shows its anonymous state;
 * - every other request completes without a response, so components keep
 *   their loading state instead of baking an error message into the HTML.
 */
@Injectable()
export class PrerenderHttpBackend implements HttpBackend {
  handle(req: HttpRequest<unknown>): Observable<HttpEvent<unknown>> {
    if (req.url.split('?')[0].endsWith('/api/auth/me')) {
      return of(new HttpResponse({ status: 200, body: { authenticated: false, user: null } }));
    }
    return EMPTY;
  }
}
