import { TestBed } from '@angular/core/testing';
import {
  InterviewerAudioService,
  LIVE_TAIL_MS,
  MIN_VOICED_MS,
  PAUSE_SIGNAL_MS,
  PREVIEW_INTERVAL_MS,
} from './interviewer-audio.service';

/** Drives the clip logic directly with synthetic PCM (no real audio graph). */
describe('InterviewerAudioService: noise clips never reach STT', () => {
  const RATE = 16_000;
  const FRAME_MS = 100;
  const frame = (amp: number) => new Float32Array((RATE * FRAME_MS) / 1000).fill(amp);

  type Internals = {
    state$: { next(v: string): void };
    pcmRate: number;
    pcmChunks: Float32Array[];
    pcmCount: number;
    _trackLevel(samples: Float32Array, durationMs: number): void;
    _cutClip(): Promise<void>;
  };

  let svc: InterviewerAudioService;
  let raw: Internals;
  let clips: number;

  beforeEach(() => {
    svc = TestBed.inject(InterviewerAudioService);
    raw = svc as unknown as Internals;
    raw.state$.next('capturing');
    raw.pcmRate = RATE;
    clips = 0;
    svc.clip$.subscribe(() => clips++);
  });

  /** Feed `loudMs` of voiced audio, padded with quiet, then end the clip. */
  async function clipWith(loudMs: number): Promise<void> {
    const push = (amp: number) => {
      const f = frame(amp);
      raw.pcmChunks.push(f);
      raw.pcmCount += f.length;
      raw._trackLevel(f, FRAME_MS);
    };
    // Quiet lead-in, kept short so the silence cut doesn't fire early.
    for (let t = 0; t < 300; t += FRAME_MS) push(0);
    for (let t = 0; t < loudMs; t += FRAME_MS) push(0.3);
    for (let t = 0; t < 500; t += FRAME_MS) push(0);
    await raw._cutClip();
  }

  it('drops a clip with only a short noise burst (click / cough / keyboard)', async () => {
    await clipWith(200);
    expect(clips).toBe(0);
  });

  it('uploads a clip with real speech', async () => {
    await clipWith(MIN_VOICED_MS + 1_000);
    expect(clips).toBe(1);
  });

  it('a dropped noise clip does not leak its voiced time into the next clip', async () => {
    await clipWith(300);
    await clipWith(200);
    expect(clips).toBe(0);
  });

  it('keeps the gate below a short 3-word question (~0.6s of speech)', () => {
    expect(MIN_VOICED_MS).toBeLessThan(600);
  });
});

/**
 * Live transcript: while the interviewer talks, snapshots of the clip so far go
 * out on preview$ (before: nothing until the sentence ended + the silence cut).
 */
describe('InterviewerAudioService: live transcript snapshots', () => {
  const RATE = 16_000;
  const FRAME_MS = 100;
  const frame = (amp: number) => new Float32Array((RATE * FRAME_MS) / 1000).fill(amp);

  type Internals = {
    state$: { next(v: string): void };
    pcmRate: number;
    pcmChunks: Float32Array[];
    pcmCount: number;
    _trackLevel(samples: Float32Array, durationMs: number): void;
    _cutClip(): Promise<void>;
  };

  let svc: InterviewerAudioService;
  let raw: Internals;
  let previews: ArrayBuffer[];
  let clips: number;

  const feed = (amp: number, ms: number) => {
    for (let t = 0; t < ms; t += FRAME_MS) {
      const f = frame(amp);
      raw.pcmChunks.push(f);
      raw.pcmCount += f.length;
      raw._trackLevel(f, FRAME_MS);
    }
  };

  beforeEach(() => {
    svc = TestBed.inject(InterviewerAudioService);
    raw = svc as unknown as Internals;
    raw.state$.next('capturing');
    raw.pcmRate = RATE;
    svc.livePreview = true;
    previews = [];
    clips = 0;
    svc.preview$.subscribe((p) => previews.push(p.data));
    svc.clip$.subscribe(() => clips++);
  });

  afterEach(() => {
    svc.livePreview = false;
    raw.state$.next('idle');
  });

  it('streams growing snapshots while the interviewer is still speaking', () => {
    feed(0.3, 3_200);
    // One per PREVIEW_INTERVAL_MS of audio, each covering MORE of the clip.
    expect(previews.length).toBe(Math.floor(3_200 / PREVIEW_INTERVAL_MS));
    for (let i = 1; i < previews.length; i++) {
      expect(previews[i].byteLength).toBeGreaterThan(previews[i - 1].byteLength);
    }
    // The question itself has not been cut yet: still exactly one clip later.
    expect(clips).toBe(0);
  });

  it('the final clip is still sent once, unchanged by the previews', async () => {
    feed(0.3, 2_500);
    feed(0, 1_000); // pause -> silence cut
    await Promise.resolve();
    expect(clips).toBe(1);
  });

  it('sends nothing for noise or during a pause', () => {
    feed(0.3, 200); // click / cough: below the voiced gate
    feed(0, 600);
    expect(previews.length).toBe(0);
  });

  it('stays off until the backend advertises live previews', () => {
    svc.livePreview = false;
    feed(0.3, 3_000);
    expect(previews.length).toBe(0);
  });

  it('keeps the cadence near real time', () => {
    expect(PREVIEW_INTERVAL_MS).toBeLessThanOrEqual(1_000);
    expect(MIN_VOICED_MS).toBeLessThan(PREVIEW_INTERVAL_MS);
  });
});

/** Pause / resume: the stream stays open, but nothing reaches STT while paused. */
describe('InterviewerAudioService: pause / resume', () => {
  type Internals = {
    state$: { next(v: string): void };
    pcmChunks: Float32Array[];
    pcmCount: number;
    listening: boolean;
  };
  let svc: InterviewerAudioService;
  let raw: Internals;

  beforeEach(() => {
    svc = TestBed.inject(InterviewerAudioService);
    raw = svc as unknown as Internals;
    raw.state$.next('capturing');
  });

  afterEach(() => {
    svc.setPaused(false);
    raw.state$.next('idle');
  });

  it('stops feeding audio while paused and drops the half-spoken clip', () => {
    raw.pcmChunks.push(new Float32Array(10));
    raw.pcmCount = 10;
    svc.setPaused(true);
    expect(svc.paused$.value).toBeTrue();
    expect(raw.listening).toBeFalse();
    expect(raw.pcmCount).toBe(0);
    expect(raw.pcmChunks.length).toBe(0);
    svc.setPaused(false);
    expect(raw.listening).toBeTrue();
  });

  it('ignores pause when nothing is being captured', () => {
    raw.state$.next('idle');
    svc.setPaused(true);
    expect(svc.paused$.value).toBeFalse();
  });
});

/** Word-by-word transcript: PCM is streamed only while someone is talking. */
describe('InterviewerAudioService: live transcript stream', () => {
  const RATE = 48_000;
  const FRAME_MS = 100;
  const frame = (amp: number) => new Float32Array((RATE * FRAME_MS) / 1000).fill(amp);

  type Internals = {
    state$: { next(v: string): void };
    pcmRate: number;
    pcmChunks: Float32Array[];
    pcmCount: number;
    _trackLevel(samples: Float32Array, durationMs: number): boolean;
    _streamLive(samples: Float32Array, durationMs: number, voiced: boolean): void;
  };

  let svc: InterviewerAudioService;
  let raw: Internals;
  let sent: ArrayBuffer[];

  const feed = (amp: number, ms: number) => {
    for (let t = 0; t < ms; t += FRAME_MS) {
      const f = frame(amp);
      raw.pcmChunks.push(f);
      raw.pcmCount += f.length;
      raw._streamLive(f, FRAME_MS, raw._trackLevel(f, FRAME_MS));
    }
  };

  beforeEach(() => {
    svc = TestBed.inject(InterviewerAudioService);
    raw = svc as unknown as Internals;
    raw.state$.next('capturing');
    raw.pcmRate = RATE;
    svc.liveStream = true;
    sent = [];
    svc.livePcm$.subscribe((f) => sent.push(f));
  });

  afterEach(() => {
    svc.liveStream = false;
    raw.state$.next('idle');
  });

  it('streams tagged 16 kHz PCM16 frames as the interviewer speaks', () => {
    feed(0.3, 500);
    expect(sent.length).toBe(5);
    const bytes = new Uint8Array(sent[0]);
    expect(String.fromCharCode(...bytes.slice(0, 4))).toBe('LPCM');
    // 100 ms at 48 kHz -> 1600 samples at 16 kHz -> 3200 bytes (+ 4-byte tag).
    expect(sent[0].byteLength).toBe(4 + 3_200);
  });

  it('sends pre-roll at onset, a tail after speech, and no silence beyond it', () => {
    feed(0, 2_000); // quiet room: nothing streamed
    expect(sent.length).toBe(0);
    feed(0.3, 300);
    const preroll = sent.length - 3;
    expect(preroll).toBeGreaterThan(0); // first syllable not clipped
    const before = sent.length;
    feed(0, 5_000);
    const tail = sent.length - before;
    expect(tail).toBe(Math.ceil(LIVE_TAIL_MS / FRAME_MS)); // provider can close the turn
  });

  it('stays off until the backend advertises the stream', () => {
    svc.liveStream = false;
    feed(0.3, 1_000);
    expect(sent.length).toBe(0);
  });

  it('signals one short pause per gap after real speech, before the clip cut', () => {
    let pauses = 0;
    svc.pause$.subscribe(() => pauses++);
    svc.liveAnswers = true;
    feed(0, 1_000); // no speech yet: silence is not a pause
    expect(pauses).toBe(0);
    feed(0.3, 1_200);
    feed(0, PAUSE_SIGNAL_MS - FRAME_MS);
    expect(pauses).toBe(0); // a breath mid-question
    feed(0, FRAME_MS);
    expect(pauses).toBe(1);
    feed(0, 300); // still the same pause
    expect(pauses).toBe(1);
    svc.liveAnswers = false;
  });

  it('sends no pause signal unless the backend answers from the stream', () => {
    let pauses = 0;
    svc.pause$.subscribe(() => pauses++);
    feed(0.3, 1_200);
    feed(0, 600);
    expect(pauses).toBe(0);
  });
});

/** "Auto Select" = the computer's sound via a loopback input, never the share picker. */
describe('InterviewerAudioService: Auto Select system audio', () => {
  let svc: InterviewerAudioService;
  let md: MediaDevices;
  const dev = (label: string, deviceId = label) =>
    ({ kind: 'audioinput', label, deviceId, groupId: '' }) as MediaDeviceInfo;
  const fakeStream = () =>
    ({ getAudioTracks: () => [{ addEventListener: () => undefined }], getTracks: () => [] }) as unknown as MediaStream;

  beforeEach(() => {
    svc = TestBed.inject(InterviewerAudioService);
    md = navigator.mediaDevices;
    spyOn(svc as unknown as { _beginCapture: () => void }, '_beginCapture');
    spyOn(md, 'getDisplayMedia');
  });

  afterEach(() => svc.state$.next('idle'));

  it('flags setup (no picker) when Stereo Mix is disabled / missing', async () => {
    spyOn(md, 'enumerateDevices').and.resolveTo([dev('Microphone Array (Intel)')]);
    spyOn(md, 'getUserMedia');
    const started = await svc.startSystemAudio();
    expect(started).toBe(false);
    expect(md.getDisplayMedia).not.toHaveBeenCalled();
    expect(md.getUserMedia).not.toHaveBeenCalled();
    expect(svc.systemAudioSetup$.value).toEqual(['Microphone Array (Intel)']);
    expect(svc.state$.value).toBe('idle');
  });

  it('captures the Stereo Mix device directly, with no picker', async () => {
    spyOn(md, 'enumerateDevices').and.resolveTo([
      dev('Microphone Array (Intel)', 'mic'),
      dev('Stereo Mix (Realtek(R) Audio)', 'mix'),
    ]);
    spyOn(md, 'getUserMedia').and.resolveTo(fakeStream());
    const started = await svc.startSystemAudio();
    expect(started).toBe(true);
    expect(md.getDisplayMedia).not.toHaveBeenCalled();
    const audio = (md.getUserMedia as jasmine.Spy).calls.mostRecent().args[0].audio;
    expect(audio.deviceId).toEqual({ exact: 'mix' });
  });

  it('recognises a localized Stereo Mix name (German "Stereomix")', async () => {
    spyOn(md, 'enumerateDevices').and.resolveTo([dev('Stereomix (Realtek(R) Audio)', 'mix')]);
    spyOn(md, 'getUserMedia').and.resolveTo(fakeStream());
    expect(await svc.startSystemAudio()).toBe(true);
  });

  it('no longer exposes a share-picker path for Auto Select', () => {
    expect((svc as unknown as Record<string, unknown>)['startScreenSystemAudio']).toBeUndefined();
  });
});
