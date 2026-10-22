import { TestBed } from '@angular/core/testing';
import { InterviewWsService } from './interview-ws.service';

/**
 * Keepalive regression.
 *
 * A silent WebSocket looks dead to proxies/NAT (which drop it) and to free-tier
 * hosts (Render spins the instance down), so the NEXT question paid a 30s+ cold
 * start — users saw this as "the AI answers very late". The socket must send an
 * application-level `ping` on a timer while it is open, and must stop the timer
 * once the socket closes so a reconnect starts exactly one keepalive.
 */
class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readyState = FakeWebSocket.OPEN;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number; reason: string; wasClean: boolean }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  readonly sent: string[] = [];

  constructor(readonly url: string) {}

  send(data: unknown): void {
    this.sent.push(typeof data === 'string' ? data : '[binary]');
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
  }

  /** Frames the service queued that are application-level pings. */
  pings(): string[] {
    return this.sent.filter((f) => f.includes('"type":"ping"'));
  }
}

/** `globalThis` narrowed so test-only globals can be swapped and restored. */
interface SwapGlobal {
  WebSocket: unknown;
  fetch: unknown;
}

const g = globalThis as unknown as SwapGlobal;

describe('InterviewWsService keepalive', () => {
  let realWebSocket: unknown;
  let realFetch: unknown;
  let sockets: FakeWebSocket[];
  let intervals: Array<{ id: number; delay: number; cb: () => void }>;
  let cleared: number[];
  let nextId: number;

  /** Let the async ticket fetch + socket construction settle. */
  const flush = async (): Promise<void> => {
    for (let i = 0; i < 6; i++) await Promise.resolve();
  };

  beforeEach(() => {
    sockets = [];
    intervals = [];
    cleared = [];
    nextId = 1;

    realWebSocket = g.WebSocket;
    realFetch = g.fetch;

    const FakeCtor = function (this: unknown, url: string): FakeWebSocket {
      const s = new FakeWebSocket(url);
      sockets.push(s);
      return s;
    } as unknown as { new (url: string): FakeWebSocket; OPEN: number };
    FakeCtor.OPEN = FakeWebSocket.OPEN;
    g.WebSocket = FakeCtor;

    // Ticket endpoint down => fall back to cookie auth, i.e. open `this.url`
    // unchanged. Keeps the spec off the network without touching the service.
    spyOn(globalThis, 'fetch').and.returnValue(
      Promise.resolve({ ok: false, status: 401 } as Response),
    );

    // Deterministic keepalive: capture the interval instead of waiting 30s.
    spyOn(window, 'setInterval').and.callFake(((cb: () => void, delay?: number) => {
      const id = nextId++;
      intervals.push({ id, delay: delay ?? 0, cb });
      return id as unknown as ReturnType<typeof setInterval>;
    }) as unknown as typeof setInterval);
    spyOn(window, 'clearInterval').and.callFake((id?: unknown) => {
      cleared.push(Number(id));
    });
  });

  afterEach(() => {
    g.WebSocket = realWebSocket;
    g.fetch = realFetch;
  });

  const connect = async (svc: InterviewWsService): Promise<FakeWebSocket> => {
    svc.connect('ws://test/ws/interview');
    await flush();
    expect(sockets.length).toBe(1);
    return sockets[0];
  };

  it('pings on a ~30s cadence while the socket is open', async () => {
    const svc = TestBed.runInInjectionContext(() => new InterviewWsService());
    const ws = await connect(svc);

    // No keepalive before the handshake completes.
    expect(intervals.length).toBe(0);

    ws.onopen!();
    expect(intervals.length).toBe(1);
    expect(intervals[0].delay).toBe(30_000);

    // Idle for two cadences -> two pings, nothing else.
    intervals[0].cb();
    intervals[0].cb();
    expect(ws.pings().length).toBe(2);

    svc.disconnect();
  });

  it('does not ping once the socket has closed', async () => {
    const svc = TestBed.runInInjectionContext(() => new InterviewWsService());
    const ws = await connect(svc);

    ws.onopen!();
    const timer = intervals[0];
    ws.onclose!({ code: 1000, reason: '', wasClean: true });

    expect(cleared).toContain(timer.id);

    // Even if the now-stale callback fires, a closed socket must not be sent.
    ws.readyState = FakeWebSocket.CLOSED;
    timer.cb();
    expect(ws.pings().length).toBe(0);

    svc.disconnect();
  });

  it('requests on-demand follow-ups and parses ai_followups replies', async () => {
    const svc = TestBed.runInInjectionContext(() => new InterviewWsService());
    const ws = await connect(svc);
    ws.onopen!();

    expect(svc.sendRequestFollowUps('What is a saga?')).toBeTrue();
    expect(ws.sent).toContain(JSON.stringify({ type: 'request_followups', question: 'What is a saga?' }));

    const got: unknown[] = [];
    svc.followUps$.subscribe((r) => got.push(r));
    ws.onmessage!({ data: JSON.stringify({ type: 'ai_followups', question: 'What is a saga?', followUps: [{ question: 'Why?', answer: 'Because.' }, { question: '' }] }) });
    ws.onmessage!({ data: JSON.stringify({ type: 'ai_followups', question: 'q', followUps: [], error: 'Could not load follow-ups. Try again.' }) });
    expect(got).toEqual([
      { question: 'What is a saga?', followUps: [{ question: 'Why?', answer: 'Because.' }], error: undefined },
      { question: 'q', followUps: [], error: 'Could not load follow-ups. Try again.' },
    ]);

    ws.readyState = FakeWebSocket.CLOSED;
    expect(svc.sendRequestFollowUps('again')).toBeFalse();
    svc.disconnect();
  });

  it('sends live-transcript snapshots only after the backend advertises them', async () => {
    const svc = TestBed.runInInjectionContext(() => new InterviewWsService());
    const ws = await connect(svc);
    ws.onopen!();
    const previews = () => ws.sent.filter((f) => f.includes('"audio_preview"'));

    // Older backend (no livePreview in the handshake) would reject the type.
    svc.sendAudioPreview(new ArrayBuffer(8), 'audio/wav');
    expect(ws.sent.length).toBe(0);

    ws.onmessage!({ data: JSON.stringify({ type: 'status', status: 'listening', message: 'connected', livePreview: true }) });
    svc.sendAudioPreview(new ArrayBuffer(8), 'audio/wav');
    expect(ws.sent).toEqual(['[binary]', JSON.stringify({ type: 'audio_preview', mimeType: 'audio/wav' })]);
    // A preview is not a question in flight: the status pill is untouched.
    expect(svc.status$.value).toBe('listening');

    // Provisional vs final transcripts keep their flag and clip seq.
    const got: unknown[] = [];
    svc.interviewerTranscript$.subscribe((t) => got.push(t));
    ws.onmessage!({ data: JSON.stringify({ type: 'stt_transcript', text: 'Can you', final: false, seq: 2 }) });
    ws.onmessage!({ data: JSON.stringify({ type: 'stt_transcript', text: 'Can you explain?', final: true, seq: 2 }) });
    expect(got).toEqual([
      { text: 'Can you', final: false, seq: 2 },
      { text: 'Can you explain?', final: true, seq: 2 },
    ]);

    // A dropped socket forgets the flag until the next handshake.
    ws.onclose!({ code: 1006, reason: '', wasClean: false });
    expect(svc.livePreview$.value).toBeFalse();
    expect(previews().length).toBe(1);
    svc.disconnect();
  });

  it('streams live PCM only when advertised and relays stt_live text', async () => {
    const svc = TestBed.runInInjectionContext(() => new InterviewWsService());
    const ws = await connect(svc);
    ws.onopen!();

    svc.sendLivePcm(new ArrayBuffer(8));
    expect(ws.sent.length).toBe(0); // older backend would glue it into a clip

    ws.onmessage!({ data: JSON.stringify({ type: 'status', status: 'listening', message: 'connected', liveStream: true }) });
    svc.sendLivePcm(new ArrayBuffer(8));
    expect(ws.sent).toEqual(['[binary]']);
    expect(svc.status$.value).toBe('listening');

    const got: unknown[] = [];
    svc.liveText$.subscribe((t) => got.push(t));
    ws.onmessage!({ data: JSON.stringify({ type: 'stt_live', text: 'Can you', final: false }) });
    ws.onmessage!({ data: JSON.stringify({ type: 'stt_live', text: 'Can you explain?', final: true }) });
    expect(got).toEqual([
      { text: 'Can you', final: false },
      { text: 'Can you explain?', final: true },
    ]);

    ws.onclose!({ code: 1006, reason: '', wasClean: false });
    expect(svc.liveStream$.value).toBeFalse();
    svc.disconnect();
  });

  it('tells the server which trial pool to use (web build)', async () => {
    const svc = TestBed.runInInjectionContext(() => new InterviewWsService());
    const ws = await connect(svc);
    expect(ws.url).toContain('client=web');
    svc.disconnect();
  });

  it('tracks trial state from the handshake and access updates', async () => {
    const svc = TestBed.runInInjectionContext(() => new InterviewWsService());
    const ws = await connect(svc);
    ws.onopen!();
    ws.onmessage!({ data: JSON.stringify({ type: 'status', status: 'listening',
      access: { type: 'access', mode: 'trial', client: 'web', remainingSeconds: 1200, limitSeconds: 1200 } }) });
    expect(svc.access$.value?.mode).toBe('trial');
    expect(svc.access$.value?.remainingSeconds).toBe(1200);
    ws.onmessage!({ data: JSON.stringify({ type: 'access', mode: 'trial', client: 'web', remainingSeconds: 600, limitSeconds: 1200 }) });
    expect(svc.access$.value?.remainingSeconds).toBe(600);
    svc.disconnect();
  });

  it('close code 4402 (trial used up) marks access denied and does NOT reconnect', async () => {
    const timeouts: number[] = [];
    spyOn(window, 'setTimeout').and.callFake(((_cb: () => void, ms?: number) => {
      timeouts.push(ms ?? 0);
      return 0 as unknown as ReturnType<typeof setTimeout>;
    }) as unknown as typeof setTimeout);
    const svc = TestBed.runInInjectionContext(() => new InterviewWsService());
    const ws = await connect(svc);
    ws.onopen!();
    ws.onclose!({ code: 4402, reason: 'trial_exhausted', wasClean: true });
    expect(svc.access$.value?.mode).toBe('denied');
    expect(timeouts.length).toBe(0); // no reconnect scheduled
    ws.onclose!({ code: 1006, reason: '', wasClean: false }); // a stray later close: still no retry
    expect(timeouts.length).toBe(0);
  });

  it('stops keepalive on disconnect', async () => {
    const svc = TestBed.runInInjectionContext(() => new InterviewWsService());
    const ws = await connect(svc);

    ws.onopen!();
    const timer = intervals[0];
    svc.disconnect();

    expect(cleared).toContain(timer.id);
    expect(svc.isConnected()).toBeFalse();
  });
});
