import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Subject } from 'rxjs';
import { AIInterviewResponse, FollowUpItem, HistoryItem, InterviewStatus } from '../models/interview.models';
import { environment } from '../../../environments/environment';
import { API_BASE_URL } from '../api/api-config';

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

interface WsMsg {
  type: string;
  [k: string]: unknown;
}

/** Live-session allowance reported by the server (see backend free_trial_service). */
export interface LiveAccess {
  mode: 'unlimited' | 'trial' | 'denied';
  client: 'web' | 'desktop';
  remainingSeconds: number;
  limitSeconds: number;
  code?: string;
  message?: string;
}

@Injectable({ providedIn: 'root' })
export class InterviewWsService {
  private readonly ticketPath = `${inject(API_BASE_URL)}/api/auth/ws-ticket`;
  messages$ = new Subject<WsMsg>();
  /**
   * Free-trial / plan state for live sessions (handshake `access` field, then
   * `{type:'access'}` updates from the server's trial meter). `denied` means
   * the trial minutes for this client are used up and a plan is needed.
   */
  access$ = new BehaviorSubject<LiveAccess | null>(null);
  status$ = new BehaviorSubject<InterviewStatus>('idle');
  guidance$ = new BehaviorSubject<AIInterviewResponse | null>(null);
  history$ = new BehaviorSubject<HistoryItem[]>([]);
  /** Model/provider that produced the latest guidance (from ai_guidance). */
  lastModel$ = new BehaviorSubject<string | null>(null);
  lastProvider$ = new BehaviorSubject<string | null>(null);
  /**
   * Provisional answer bullets streamed token-by-token (ai_stream_delta).
   * These are a PREVIEW only — the validated ai_guidance card replaces them.
   * Never render these as the final answer.
   */
  streamPoints$ = new BehaviorSubject<string[]>([]);
  /** True while a streamed answer is in flight (ai_stream_start → ai_guidance). */
  streaming$ = new BehaviorSubject<boolean>(false);
  /**
   * Non-fatal warnings. `scope` separates AI warnings (template guidance is
   * shown instead) from speech-to-text warnings (no fallback exists).
   */
  notice$ = new Subject<{ message: string; scope: 'ai' | 'stt' }>();
  /**
   * On-demand "If they ask further" results (ai_followups), requested with
   * sendRequestFollowUps(). `error` is set when none could be generated.
   */
  followUps$ = new Subject<{ question: string; followUps: FollowUpItem[]; error?: string }>();
  private ws: WebSocket | null = null;
  private retries = 0;
  private url = this.defaultUrl();
  private wantOpen = false;
  /** Guards against overlapping reconnect attempts while a ticket is fetched. */
  private opening = false;
  private pingTimer: ReturnType<typeof setInterval> | null = null;

  /**
   * Keepalive cadence. An idle WebSocket looks like a dead connection to
   * proxies/NAT (they drop it silently) and to free-tier hosts (Render spins
   * the instance down, so the NEXT question pays a 30s+ cold start). A tiny
   * application-level ping every 30s keeps the pipe hot and surfaces a dead
   * socket early instead of on the next question.
   */
  private static readonly PING_INTERVAL_MS = 30_000;
  /** Server close code: the free live-interview minutes are used up. */
  static readonly TRIAL_EXHAUSTED_CODE = 4402;

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
      const res = await fetch(this.ticketPath, {
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
    this.access$.next(null); // re-learned from the handshake (a plan may have been bought)
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
    // Which free-trial pool to use: the desktop app and the website each have
    // their own minutes (see backend free_trial_service).
    const client = `client=${environment.electron ? 'desktop' : 'web'}`;
    const sep = this.url.includes('?') ? '&' : '?';
    if (!ticket) return `${this.url}${sep}${client}`;
    return `${this.url}${sep}ticket=${encodeURIComponent(ticket)}&${client}`;
  }

  private attachHandlers(): void {
    if (!this.ws) return;
    this.ws.onopen = () => {
      this.retries = 0;
      // A successful (re)connect clears a previous "Connection issue" pill so
      // the UI recovers automatically instead of staying stuck on error.
      if (this.status$.value === 'error') this.status$.next('idle');
      this.startPing();
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
          this.lastModel$.next(typeof msg['model'] === 'string' ? msg['model'] : null);
          this.lastProvider$.next(
            typeof msg['provider'] === 'string' ? msg['provider'] : null,
          );
          // Final validated card replaces any provisional preview.
          this.streaming$.next(false);
          this.streamPoints$.next([]);
          this.status$.next('ready');
        } else if (msg.type === 'ai_stream_start') {
          // A streamed answer began: show which model is generating, live.
          this.streaming$.next(true);
          this.streamPoints$.next([]);
          if (typeof msg['model'] === 'string') this.lastModel$.next(msg['model']);
          if (typeof msg['provider'] === 'string') this.lastProvider$.next(msg['provider']);
        } else if (msg.type === 'ai_stream_delta') {
          const point = typeof msg['point'] === 'string' ? msg['point'] : '';
          if (point) this.streamPoints$.next([...this.streamPoints$.value, point]);
        } else if (msg.type === 'status') {
          // The handshake advertises live transcript support (older backends
          // omit it and would reject audio_preview as an unknown type).
          if (typeof msg['livePreview'] === 'boolean') this.livePreview$.next(msg['livePreview']);
          if (typeof msg['liveStream'] === 'boolean') this.liveStream$.next(msg['liveStream']);
          if (typeof msg['liveAnswers'] === 'boolean') this.liveAnswers$.next(msg['liveAnswers']);
          if (msg['access'] && typeof msg['access'] === 'object') {
            this.access$.next(msg['access'] as unknown as LiveAccess);
          }
          // Only the handshake carries the feature flags: a new connection.
          if (typeof msg['liveStream'] === 'boolean') this.handshake$.next();
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
        } else if (msg.type === 'access') {
          this.access$.next(msg as unknown as LiveAccess);
        } else if (msg.type === 'ai_followups') {
          const items = Array.isArray(msg['followUps']) ? (msg['followUps'] as FollowUpItem[]) : [];
          this.followUps$.next({
            question: String(msg['question'] ?? ''),
            followUps: items.filter((f) => !!f?.question),
            error: typeof msg['error'] === 'string' ? msg['error'] : undefined,
          });
        } else if (msg.type === 'stt_transcript') {
          // Interviewer audio recognised by the server-side STT provider.
          // final:false = provisional live text for the clip still being spoken.
          this.interviewerTranscript$.next({
            text: String(msg['text'] ?? ''),
            final: msg['final'] !== false,
            seq: typeof msg['seq'] === 'number' ? msg['seq'] : undefined,
          });
        } else if (msg.type === 'stt_live') {
          // Word-by-word streamed text of the current utterance (display only).
          this.liveText$.next({ text: String(msg['text'] ?? ''), final: msg['final'] === true });
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
          // A failed stream stops, but already-shown provisional bullets stay
          // visible so Retry has context.
          this.streaming$.next(false);
          this.status$.next('error');
        }
      } catch (err) {
        // A malformed frame is a client/server contract bug, not ignorable noise.
        console.warn('[ws] could not parse message:', (err as Error)?.message, String(ev.data).slice(0, 200));
      }
    };
    this.ws.onclose = (ev: CloseEvent) => {
      if (ev.code === InterviewWsService.TRIAL_EXHAUSTED_CODE) {
        // Free minutes used up: reconnecting would only be refused again.
        console.warn('[ws] closed: free trial used up; not reconnecting');
        this.stopPing();
        this.wantOpen = false;
        const a = this.access$.value;
        if (a?.mode !== 'denied') {
          this.access$.next({ mode: 'denied', client: a?.client ?? 'web', remainingSeconds: 0, limitSeconds: a?.limitSeconds ?? 0, code: 'TRIAL_EXHAUSTED' });
        }
        this.status$.next('idle');
        return;
      }
      // Close codes explain WHY the socket died (1008 = auth, 1000 = normal).
      console.warn(
        `[ws] closed code=${ev.code} reason=${ev.reason || '-'} clean=${ev.wasClean}; retrying`,
      );
      this.stopPing();
      this.livePreview$.next(false); // re-learned from the next handshake
      this.liveStream$.next(false);
      this.liveAnswers$.next(false);
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

  /** Start the application-level keepalive (see PING_INTERVAL_MS). */
  private startPing(): void {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        try {
          this.ws.send(JSON.stringify({ type: 'ping' }));
        } catch {
          /* socket raced closed; onclose will retry */
        }
      }
    }, InterviewWsService.PING_INTERVAL_MS);
  }

  private stopPing(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  // ------------------------------------------------ interviewer (tab/meeting) audio

  /** Interviewer speech recognised by the server-side STT provider. */
  interviewerTranscript$ = new Subject<{ text: string; final: boolean; seq?: number }>();
  /** True when the backend accepts audio_preview snapshots (live transcript). */
  livePreview$ = new BehaviorSubject<boolean>(false);
  /** True when the backend accepts streamed PCM (word-by-word transcript). */
  liveStream$ = new BehaviorSubject<boolean>(false);
  /** True when the backend answers from the live stream (accepts speech_pause). */
  liveAnswers$ = new BehaviorSubject<boolean>(false);
  /** Streamed transcript of the current utterance; `final` closes the turn. */
  liveText$ = new Subject<{ text: string; final: boolean }>();
  /**
   * Fires once per (re)connection, after its handshake. The server keeps
   * capture state PER SOCKET: a new socket ignores all meeting audio until it
   * is told `interviewer_audio_start` again.
   */
  handshake$ = new Subject<void>();

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
  sendAudioClip(data: ArrayBuffer, mimeType: string, responseLength = 'default', model?: string): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(data);
    // Following control frame tells the backend "clip complete, transcribe now".
    this.ws.send(JSON.stringify({ type: 'audio_flush', mimeType, responseLength, model }));
    this.status$.next('processing');
  }

  /**
   * Snapshot of the clip still being spoken (live transcript). Binary frame +
   * control frame are sent back to back, so they never interleave with a clip.
   * Does not touch status$: a preview is not a question being processed.
   */
  sendAudioPreview(data: ArrayBuffer, mimeType: string): void {
    if (this.ws?.readyState !== WebSocket.OPEN || !this.livePreview$.value) return;
    this.ws.send(data);
    this.ws.send(JSON.stringify({ type: 'audio_preview', mimeType }));
  }

  /** One tagged live-transcript PCM frame (already carries the "LPCM" tag). */
  sendLivePcm(frame: ArrayBuffer): void {
    if (this.ws?.readyState !== WebSocket.OPEN || !this.liveStream$.value) return;
    this.ws.send(frame);
  }

  /**
   * The interviewer just paused (~0.45 s). Lets the backend answer a finished
   * sentence from the live transcript without waiting for the clip cut.
   * Older backends reject unknown types, so it is gated on liveAnswers.
   */
  sendSpeechPause(responseLength = 'default'): void {
    if (this.ws?.readyState !== WebSocket.OPEN || !this.liveAnswers$.value) return;
    this.ws.send(JSON.stringify({ type: 'speech_pause', responseLength }));
  }

  /** Manual interviewer question -> existing AI flow. */
  sendInterviewerQuestion(text: string, responseLength = 'default', model?: string): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'interviewer_question', text, responseLength, model }));
      this.status$.next('thinking');
    }
  }

  sendTranscript(text: string, responseLength = 'default', model?: string): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'transcript', text, responseLength, model }));
      this.status$.next('thinking');
    }
  }

  sendForce(question: string, responseLength = 'default', model?: string): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      // Text was already streamed via sendTranscript; the server analyzes its
      // buffer. `text` is included only as a fallback if the buffer is empty.
      this.ws.send(JSON.stringify({ type: 'force', text: question, responseLength, model }));
      this.status$.next('thinking');
    }
  }

  /**
   * Ask for "If they ask further" for an answered question. Returns false when
   * the socket is not open (the caller shows an error instead of spinning).
   */
  sendRequestFollowUps(question: string): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify({ type: 'request_followups', question }));
    return true;
  }

  /** Switch the AI model for subsequent answers (mid-session). */
  sendSetModel(model: string): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'set_model', model }));
    }
  }

  sendReset(): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'reset' }));
    }
    this.guidance$.next(null);
    this.history$.next([]);
    this.streamPoints$.next([]);
    this.streaming$.next(false);
    this.status$.next('idle');
  }

  /** Close the socket and forget the session's answers (used on sign-out). */
  clearSession(): void {
    this.disconnect();
    this.retries = 0;
    this.guidance$.next(null);
    this.history$.next([]);
    this.streamPoints$.next([]);
    this.streaming$.next(false);
    this.status$.next('idle');
  }

  pushLocalGuidance(g: AIInterviewResponse): void {
    this.guidance$.next(g);
    this.history$.next([...this.history$.value, { question: g.question, guidance: g, timestamp: new Date() }]);
    this.streamPoints$.next([]);
    this.streaming$.next(false);
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
    this.stopPing();
    try { this.ws?.close(); } catch { /* noop */ }
    this.ws = null;
  }
}
