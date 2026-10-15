import { HttpClient } from '@angular/common/http';
import { Injectable, NgZone, OnDestroy, inject } from '@angular/core';
import { NavigationEnd, Router } from '@angular/router';
import { Subscription, filter } from 'rxjs';
import { environment } from '../../../environments/environment';

const BASE = environment.apiBaseUrl;
/** Long stays are banked piecemeal so crashes still count partial time. */
const HEARTBEAT_SECONDS = 30;
/** Click batches leave the browser at most this often. */
const CLICK_FLUSH_MS = 15000;
/** One beacon never claims more than this (idle background tabs). */
const MAX_BEACON_SECONDS = 600;
const MAX_QUEUE = 200;

interface QueuedClick {
  path: string;
  label: string;
  target: string;
}

/**
 * Anonymous product analytics: how long each page is viewed and which
 * labeled buttons/links get pressed. Privacy rules:
 * - Honors Do-Not-Track (tracking fully off when DNT=1).
 * - Click labels only — input values, passwords and keystrokes are never read.
 * - Everything fire-and-forget; failures are silent and never disturb the UI.
 */
@Injectable({ providedIn: 'root' })
export class ActivityService implements OnDestroy {
  private http = inject(HttpClient);
  private router = inject(Router);
  private zone = inject(NgZone);
  private subs: Subscription[] = [];

  private readonly on: boolean;
  private currentPath = '/';
  private enteredAt = 0;
  private clickQueue: QueuedClick[] = [];
  private hbTimer: ReturnType<typeof setInterval> | null = null;
  private clickTimer: ReturnType<typeof setInterval> | null = null;
  private onClickBound = (ev: MouseEvent) => this.onClick(ev);
  private onVisBound = () => this.onVisibility();
  private onHideBound = () => this.onPageHide();

  constructor() {
    this.on =
      typeof navigator !== 'undefined' &&
      typeof document !== 'undefined' &&
      navigator.doNotTrack !== '1';
    if (!this.on) return;
    this.currentPath = this.cleanPath(this.router.url || location.pathname);
    this.enteredAt = Date.now();
    this.subs.push(
      this.router.events
        .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
        .subscribe((e) => this.onNavigate(e.urlAfterRedirects)),
    );
    document.addEventListener('click', this.onClickBound, true);
    document.addEventListener('visibilitychange', this.onVisBound);
    window.addEventListener('pagehide', this.onHideBound);
    // Timers run outside Angular so analytics never triggers change detection.
    this.zone.runOutsideAngular(() => {
      this.hbTimer = setInterval(() => this.onHeartbeat(), HEARTBEAT_SECONDS * 1000);
      this.clickTimer = setInterval(() => this.flushClicks(), CLICK_FLUSH_MS);
    });
  }

  ngOnDestroy(): void {
    this.subs.forEach((s) => s.unsubscribe());
    if (this.hbTimer) clearInterval(this.hbTimer);
    if (this.clickTimer) clearInterval(this.clickTimer);
    document.removeEventListener('click', this.onClickBound, true);
    document.removeEventListener('visibilitychange', this.onVisBound);
    window.removeEventListener('pagehide', this.onHideBound);
  }

  // ------------------------------------------------------------------ paging

  private cleanPath(url: string): string {
    return (url || '/').split(/[?#]/)[0] || '/';
  }

  private elapsedSeconds(): number {
    return Math.max(
      0,
      Math.min(MAX_BEACON_SECONDS, Math.floor((Date.now() - this.enteredAt) / 1000)),
    );
  }

  private onNavigate(url: string): void {
    this.bankTime();
    this.currentPath = this.cleanPath(url);
    this.enteredAt = Date.now();
  }

  private onHeartbeat(): void {
    // Bank elapsed time but keep the page "open" for the next interval.
    const seconds = this.elapsedSeconds();
    if (seconds > 0) this.postTime(this.currentPath, seconds);
    this.enteredAt = Date.now();
  }

  private bankTime(): void {
    const seconds = this.elapsedSeconds();
    if (seconds > 0) this.postTime(this.currentPath, seconds);
  }

  private postTime(path: string, seconds: number): void {
    this.http
      .post(`${BASE}/api/activity/page-time`, { path, seconds })
      .subscribe({ error: () => undefined });
  }

  private onVisibility(): void {
    if (document.hidden) {
      this.bankTime();
      this.flushClicks();
    } else {
      this.enteredAt = Date.now();
    }
  }

  private onPageHide(): void {
    // Synchronous transports only during unload: sendBeacon survives it.
    const seconds = this.elapsedSeconds();
    try {
      if (seconds > 0) {
        navigator.sendBeacon(
          `${BASE}/api/activity/page-time`,
          new Blob([JSON.stringify({ path: this.currentPath, seconds })], {
            type: 'application/json',
          }),
        );
      }
      if (this.clickQueue.length) {
        const events = this.clickQueue.splice(0);
        navigator.sendBeacon(
          `${BASE}/api/activity/clicks`,
          new Blob([JSON.stringify({ events })], { type: 'application/json' }),
        );
      }
    } catch {
      /* unload path: never throw */
    }
  }

  // ------------------------------------------------------------------ clicks

  private onClick(ev: MouseEvent): void {
    const node = ev.target as HTMLElement | null;
    const el = node?.closest?.('button, a, [data-track]') as HTMLElement | null;
    if (!el) return;
    // Never read values — label is aria-label, title or visible text only.
    const label = (
      el.getAttribute('aria-label') ||
      el.getAttribute('title') ||
      (el.innerText || '')
    )
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 80);
    const target = `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}${
      el.classList.length ? '.' + el.classList[0] : ''
    }`.slice(0, 80);
    this.clickQueue.push({ path: this.currentPath, label, target });
    if (this.clickQueue.length > MAX_QUEUE) {
      this.clickQueue.splice(0, this.clickQueue.length - MAX_QUEUE);
    }
  }

  private flushClicks(): void {
    if (!this.clickQueue.length) return;
    const events = this.clickQueue.splice(0);
    this.http
      .post(`${BASE}/api/activity/clicks`, { events })
      .subscribe({ error: () => undefined });
  }
}
