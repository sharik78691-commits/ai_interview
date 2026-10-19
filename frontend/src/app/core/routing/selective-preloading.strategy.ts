import { Injectable } from '@angular/core';
import { PreloadingStrategy, Route } from '@angular/router';
import { Observable, of } from 'rxjs';

/**
 * Preloads only lazy routes flagged with `data: { preload: true }`.
 *
 * `PreloadAllModules` would also pull admin/legal/pricing chunks for every
 * visitor; this keeps the background download to the core signed-in flow
 * (dashboard + interview). Preloading starts after the initial navigation
 * completes, so it never competes with first paint.
 */
@Injectable({ providedIn: 'root' })
export class SelectivePreloadingStrategy implements PreloadingStrategy {
  preload(route: Route, load: () => Observable<unknown>): Observable<unknown> {
    return route.data?.['preload'] === true ? load() : of(null);
  }
}
