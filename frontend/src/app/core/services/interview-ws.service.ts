import { Injectable } from '@angular/core';
import { BehaviorSubject, Subject } from 'rxjs';
import { AIInterviewResponse, HistoryItem, InterviewStatus } from '../models/interview.models';

/**
 * Backend origin for the live-interview WebSocket, read from the runtime config
 * (`public/runtime-config.js` sets `window.WS_BASE_URL` before the app boots).
 *
 * Static hosts (Vercel) cannot proxy a WebSocket upgrade to an external origin,
 * so in production this MUST point at the backend host (e.g.
 * `wss://ai-interview-1-309j.onrender.com`). When empty, the socket falls back
 * to the same-origin `/ws/interview` path, which the Angular dev-server proxy
 * forwards to the backend during local development.
 */
const WS_BASE_URL: string =
  (typeof window !== 'undefined' &&
    (window as unknown as { WS_BASE_URL?: string }).WS_BASE_URL) ||
  '';

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

  /**
   * WebSocket URL for the live-interview socket.
   *
   * The browser must reach the backend directly for the socket upgrade: static
   * hosts (Vercel) cannot proxy a WebSocket to an external origin, so a
   * same-origin `/ws` path only works behind the Angular dev-server proxy.
   *
   * Resolution order:
   *   1. `WS_BASE_URL` build-time env (e.g. `wss://api.example.com`) — used in
   *      production so the socket targets the backend host directly.
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

  connect(url?: string): void {
    this.url = url ?? this.defaultUrl();
    this.wantOpen = true;
    this.open();
  }

  private open(): void {
    if (!this.wantOpen) return;
    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
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
          this.status$.next('error');
        }
      } catch {
        /* ignore malformed */
      }
    };
    this.ws.onclose = () => this.scheduleReconnect();
    this.ws.onerror = () => {
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
