import { Injectable } from '@angular/core';
import { BehaviorSubject, Subject } from 'rxjs';
import { AIInterviewResponse, HistoryItem, InterviewStatus } from '../models/interview.models';
import { environment } from '../../../environments/environment';

/**
 * Backend origin for the live-interview WebSocket.
 *
 * Resolution order:
 *   1. `window.WS_BASE_URL` — optional runtime override (rarely used; kept so a
 *      host can retarget the socket without rebuilding).
 *   2. `environment.wsBaseUrl` — build-time value, swapped automatically:
 *      - local  : '' (same-origin `/ws/interview`, forwarded by the Angular
 *        dev-server proxy to http://localhost:8000)
 *      - prod   : 'wss://ai-interview-1-309j.onrender.com' (direct connection,
 *        because static hosts cannot reliably proxy a WebSocket upgrade).
 */
const WS_BASE_URL: string =
  (typeof window !== 'undefined' &&
    (window as unknown as { WS_BASE_URL?: string }).WS_BASE_URL) ||
  environment.wsBaseUrl ||
  '';

/**
 * Same-origin endpoint that mints a short-lived WebSocket ticket.
 *
 * This is deliberately a RELATIVE path: the SPA is served from
 * `oyeinterview.com` and `/api` is proxied to the backend (Vercel rewrite),
 * so the request is same-origin and therefore DOES carry the session cookie.
 * The socket itself is cross-origin and does not, which is exactly why the
 * ticket exists.
 */
const TICKET_PATH = `${environment.apiBaseUrl}/api/auth/ws-ticket`;

interface WsMsg {
  type: string;
  [k: string]: unknown;
}

@Injectable({ providedIn: 'root' })
export class InterviewWsService {
  messages$ = new Subject<WsMsg>();
  status$ = new BehaviorSubject<InterviewStatus>('idle');
  guidance$ = new BehaviorSubject<AIInterviewResponse | null>(null);
  history$ = new BehaviorSubject<HistoryItem[]>([]);
  /**
   * Non-fatal warnings. `scope` separates AI warnings (template guidance is
   * shown instead) from speech-to-text warnings (no fallback exists).
   */
  notice$ = new Subject<{ message: string; scope: 'ai' | 'stt' }>();
  private ws: WebSocket | null = null;
  private retries = 0;
  private url = this.defaultUrl();
  private wantOpen = false;
  /** Guards against overlapping reconnect attempts while a ticket is fetched. */
  private opening = false;

  /**
   * WebSocket URL for the live-interview socket.
   *
   * The browser must reach the backend directly for the socket upgrade: static
   * hosts (Vercel) cannot proxy a WebSocket to an external origin, so a
   * same-origin `/ws` path only works behind the Angular dev-server proxy.
   *
   * Resolution order:
   *   1. `environment.wsBaseUrl` build-time value (production build) — the socket
   *      targets the backend host directly, e.g. `wss://api.example.com`.
   *   2. Same-origin `/ws/interview` — used in local dev, where the Angular
   *      proxy forwards `/ws` to the backend and keeps the session cookie
   *      same-origin.
   */
  private defaultUrl(): string {
    if (typeof window === 'undefined') return '/ws/interview';
    const configured = (WS_BASE_URL || '').trim().replace(/\/+$/, '');
    if (configured) {
      // Accept either a full ws(s):// URL or an http(s):// one and normalise it.
      const base = configured
        .replace(/^https:\/\//i, 'wss://')
        .replace(/^http:\/\//i, 'ws://');
      return `${base}/ws/interview`;
    }
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}/ws/interview`;
  }

  /**
   * Fetch a short-lived ticket so the cross-origin socket can authenticate.
   *
   * Browsers do not send cookies on cross-origin WebSocket handshakes, so the
   * session cookie that authenticates every REST call is absent on the socket
   * (the server then rejects the handshake with 403/1008). This request is
   * same-origin, so it DOES carry the cookie.
   *
   * @returns the ticket, or null when the session is missing/expired or the
   *          endpoint is unreachable. Callers fall back to cookie auth, which
   *          still works same-origin (local dev via the Angular proxy).
   */
  private async fetchTicket(): Promise<string | null> {
    try {
      const res = await fetch(TICKET_PATH, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!res.ok) {
        // 401 => not signed in (or the session cookie was not sent). Log the
        // status because the socket failure alone is an opaque 403.
        console.warn(
          `[ws] ticket request failed: HTTP ${res.status}. ` +
            'Ensure you are signed in; the socket will fall back to cookie auth.',
        );
        return null;
      }
      const body = (await res.json()) as { ticket?: string };
      if (!body?.ticket) {
        console.warn('[ws] ticket request returned no ticket field');
        return null;
      }
      return body.ticket;
    } catch (err) {
      console.warn('[ws] ticket request errored; falling back to cookie auth', err);
      return null;
    }
  }

  connect(url?: string): void {
    this.url = url ?? this.defaultUrl();
    this.wantOpen = true;
    void this.open();
  }

  private async open(): Promise<void> {
    if (!this.wantOpen) return;
    // A ticket fetch is async, so a slow response plus a pending backoff timer
    // could otherwise start two sockets.
    if (this.opening) return;
    this.opening = true;

    try {
      const target = await this.buildSocketUrl();
      if (!this.wantOpen) return;
      try {
        this.ws = new WebSocket(target);
      } catch (err) {
        console.warn('[ws] could not construct WebSocket', err);
        this.scheduleReconnect();
        return;
      }
      this.attachHandlers();
    } finally {
      this.opening = false;
    }
  }

  /**
   * Append a fresh ticket to the socket URL.
   *
   * The ticket is minted per connection attempt rather than reused, because it
   * expires quickly (default 60s) and a Render cold start can easily outlast
   * that. Query strings are visible to proxies, so it is never persisted.
   */
  private async buildSocketUrl(): Promise<string> {
    const ticket = await this.fetchTicket();
    if (!ticket) return this.url;
    const sep = this.url.includes('?') ? '&' : '?';
    return `${this.url}${sep}ticket=${encodeURIComponent(ticket)}`;
  }

  private attachHandlers(): void {
    if (!this.ws) return;
    this.ws.onopen = () => {
      this.retries = 0;
      // A successful (re)connect clears a previous "Connection issue" pill so
      // the UI recovers automatically instead of staying stuck on error.
      if (this.status$.value === 'error') this.status$.next('idle');
    };
    this.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as WsMsg;
        this.messages$.next(msg);
        if (msg.type === 'ai_guidance') {
          const g = (msg['data'] ?? msg['guidance']) as AIInterviewResponse;
          if (!g) return;
          this.guidance$.next(g);
          this.history$.next([
            ...this.history$.value,
            { question: g.question, guidance: g, timestamp: new Date() },
          ]);
          this.status$.next('ready');
        } else if (msg.type === 'status') {
          const s = msg['status'] as string | undefined;
          if (s === 'idle' || s === 'listening' || s === 'processing' || s === 'thinking' || s === 'ready' || s === 'paused' || s === 'error') {
            this.status$.next(s);
          } else {
            // Older backend sends only {type:'status', message} — infer instead of
            // defaulting to 'processing' so the pill doesn't get stuck.
            const m = String(msg['message'] ?? '');
            if (/question detected|thinking/i.test(m)) this.status$.next('processing');
            else if (/listening|connected|reset/i.test(m)) this.status$.next('listening');
          }
        } else if (msg.type === 'stt_transcript') {
          // Interviewer audio recognised by the server-side STT provider.
          this.interviewerTranscript$.next({
            text: String(msg['text'] ?? ''),
            final: msg['final'] !== false,
          });
        } else if (msg.type === 'notice') {
          // AI fallback (quota/rate limit) vs. speech-to-text problem.
          const scope = msg['scope'] === 'stt' ? 'stt' : 'ai';
          this.notice$.next({
            message: String(msg['message'] ?? 'AI service unavailable.'),
            scope,
          });
        } else if (msg.type === 'error') {
          // Server-side failure relayed over the socket (AI/STT unavailable...).
          console.error(
            `[ws] server error: ${String(msg['message'] ?? 'unknown')}`,
          );
          this.status$.next('error');
        }
      } catch (err) {
        // A malformed frame is a client/server contract bug, not ignorable noise.
        console.warn('[ws] could not parse message:', (err as Error)?.message, String(ev.data).slice(0, 200));
      }
    };
    this.ws.onclose = (ev: CloseEvent) => {
      // Close codes explain WHY the socket died (1008 = auth, 1000 = normal).
      console.warn(
        `[ws] closed code=${ev.code} reason=${ev.reason || '-'} clean=${ev.wasClean}; retrying`,
      );
      this.scheduleReconnect();
    };
    this.ws.onerror = (ev: Event) => {
      console.error('[ws] socket error', ev);
      this.status$.next('error');
    };
  }

  private scheduleReconnect(): void {
    if (!this.wantOpen) return;
    // Keep retrying with a capped backoff. A free-tier backend (Render) can
    // cold-start for 30s+, so giving up after a few tries left the UI stuck on
    // "Connection issue" even though the server was about to come back.
    this.retries++;
    const backoff = Math.min(1000 * 2 ** Math.min(this.retries, 5), 15000);
    setTimeout(() => this.open(), backoff);
  }

  // ------------------------------------------------ interviewer (tab/meeting) audio

  /** Interviewer speech recognised by the server-side STT provider. */
  interviewerTranscript$ = new Subject<{ text: string; final: boolean }>();

  /** Tell the backend we are starting to stream interviewer audio. */
  sendInterviewerAudioStart(mimeType: string): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'interviewer_audio_start', mimeType }));
    }
  }

  /** Tell the backend we stopped streaming interviewer audio. */
  sendInterviewerAudioStop(): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'interviewer_audio_stop' }));
    }
  }

  /** Stream one interviewer audio clip as a binary frame. */
  sendAudioClip(data: ArrayBuffer, mimeType: string, responseLength = 'medium'): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(data);
    // Following control frame tells the backend "clip complete, transcribe now".
    this.ws.send(JSON.stringify({ type: 'audio_flush', mimeType, responseLength }));
    this.status$.next('processing');
  }

  /** Manual interviewer question -> existing AI flow. */
  sendInterviewerQuestion(text: string, responseLength = 'medium'): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'interviewer_question', text, responseLength }));
      this.status$.next('thinking');
    }
  }

  sendTranscript(text: string, responseLength = 'medium'): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'transcript', text, responseLength }));
      this.status$.next('thinking');
    }
  }

  sendForce(question: string, responseLength = 'medium'): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      // Text was already streamed via sendTranscript; the server analyzes its
      // buffer. `text` is included only as a fallback if the buffer is empty.
      this.ws.send(JSON.stringify({ type: 'force', text: question, responseLength }));
      this.status$.next('thinking');
    }
  }

  sendReset(): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'reset' }));
    }
    this.guidance$.next(null);
    this.history$.next([]);
    this.status$.next('idle');
  }

  /** Close the socket and forget the session's answers (used on sign-out). */
  clearSession(): void {
    this.disconnect();
    this.retries = 0;
    this.guidance$.next(null);
    this.history$.next([]);
    this.status$.next('idle');
  }

  pushLocalGuidance(g: AIInterviewResponse): void {
    this.guidance$.next(g);
    this.history$.next([...this.history$.value, { question: g.question, guidance: g, timestamp: new Date() }]);
    this.status$.next('ready');
  }

  setStatus(s: InterviewStatus): void {
    this.status$.next(s);
  }

  isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  disconnect(): void {
    this.wantOpen = false;
    try { this.ws?.close(); } catch { /* noop */ }
    this.ws = null;
  }
}
