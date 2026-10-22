import { Injectable } from '@angular/core';
import { BehaviorSubject, Subject } from 'rxjs';
import { pcmToWav, toPcm16, toWav } from './wav-encoder';

/**
 * InterviewerAudioService
 * ---------------------------------------------------------------------------
 * Captures the MEETING / TAB audio chosen by the user via the browser's
 * "Share" picker (getDisplayMedia with audio).
 *
 * Important limitation, by design:
 *  - The Web Speech API can ONLY listen to the microphone, so tab/meeting
 *    audio cannot be transcribed inside the browser. That is why this service
 *    records short clips and sends the raw bytes to the backend, where the
 *    Speech-to-Text provider (Whisper) turns them into text.
 *  - This audio source is completely separate from the interviewee's own
 *    microphone (see AudioService / TranscriptionService).
 *
 * Clip lifecycle: a single MediaRecorder runs for the whole session and is never
 * interrupted. Silence detection (AudioContext energy check) decides when the
 * interviewer has finished a sentence; the accumulated chunks are then handed to
 * the WebSocket as one clip and a new clip window begins. Each clip is decoded
 * in the browser and re-encoded as 16 kHz mono WAV, because the raw "live" WebM
 * that MediaRecorder produces is rejected by some transcription APIs.
 */
@Injectable({ providedIn: 'root' })
export class InterviewerAudioService {
  /** 'idle' | 'prompting' | 'capturing' | 'stopping' */
  readonly state$ = new BehaviorSubject<'idle' | 'prompting' | 'capturing' | 'stopping'>('idle');
  /** True when the browser can capture tab/meeting audio at all. */
  readonly supported = typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getDisplayMedia;

  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private audioCtx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private energyTimer: ReturnType<typeof setInterval> | null = null;
  private silentTicks = 0;
  private clipStartedAt = 0;
  private stopPromise: Promise<void> | null = null;
  /** Raw PCM pulled off the shared stream (the transcription source). */
  private pcmChunks: Float32Array[] = [];
  private pcmCount = 0;
  private pcmRate = 0;
  /** Milliseconds of continuous quiet within the current clip. */
  private silentMs = 0;
  private pcmProcessor: ScriptProcessorNode | null = null;
  /** True when raw PCM capture is running (preferred path). */
  private hasPcmCapture = false;
  /** Chunks of the clip currently being accumulated (chunk 0 holds the header). */
  private pendingChunks: Blob[] = [];
  private pendingCount = 0;
  private clipHasSpeech = false;
  /** Total above-threshold audio in the current clip (ms). */
  private voicedMs = 0;
  private cutting = false;
  private recordedMime = 'audio/webm';

  /** Emitted audio clips: raw bytes + mime type, ready for the WebSocket. */
  readonly clip$ = new Subject<{ data: ArrayBuffer; mimeType: string }>();
  /**
   * Live transcript: WAV snapshots of the clip STILL being spoken, every
   * PREVIEW_INTERVAL_MS of audio while the interviewer talks. Display only —
   * the clip itself is still cut and sent once via clip$.
   */
  readonly preview$ = new Subject<{ data: ArrayBuffer; mimeType: string }>();
  /** Set by the page when the backend advertises live previews. */
  livePreview = false;
  /** Clip time (ms) of the last preview snapshot. */
  private lastPreviewMs = 0;
  /**
   * Word-by-word transcript: tagged 16 kHz PCM16 frames, streamed only while
   * someone is talking (plus pre-roll and a short tail so the provider hears
   * the first syllable and the end of the turn). Display only.
   */
  readonly livePcm$ = new Subject<ArrayBuffer>();
  /** Set by the page when the backend advertises the live stream. */
  liveStream = false;
  private liveOpen = false;
  private liveTailMs = 0;
  private livePreroll: Float32Array[] = [];
  /**
   * Fires once per pause of PAUSE_SIGNAL_MS after real speech. The backend
   * answers a FINISHED sentence of the live transcript on it — the way a
   * person replies after a short beat — instead of waiting for the clip cut.
   */
  readonly pause$ = new Subject<void>();
  /** Set by the page when the backend answers from the live stream. */
  liveAnswers = false;
  /**
   * Paused: capture stays open, but every sample is dropped before it reaches
   * clips, previews or the live stream — nothing goes to STT or the AI.
   */
  readonly paused$ = new BehaviorSubject<boolean>(false);
  /** User-facing status message for the UI. */
  readonly status$ = new BehaviorSubject<string>('');
  /**
   * Non-null when "Auto Select" needs the user to enable a system-audio
   * (loopback) device first. Carries the audio-input labels the browser can
   * currently see, so the UI can show exactly what's missing.
   */
  readonly systemAudioSetup$ = new BehaviorSubject<string[] | null>(null);

  /** Consecutive silent checks (~100ms each) that end a clip. */
  private readonly silenceTicksToCut = 10;
  /** RMS below this counts as silence (re-derived live from the noise floor). */
  private silenceThreshold = DEFAULT_SILENCE_THRESHOLD;
  /** Slow-moving estimate of the source's background level. */
  private noiseFloor = DEFAULT_SILENCE_THRESHOLD / NOISE_MARGIN;

  /**
   * Why tab/meeting audio cannot be used, or null when it looks supported.
   * Shown in the UI so the user gets a specific message instead of a generic
   * "unavailable" error.
   */
  getBlockReason(): string | null {
    const nav = typeof navigator !== 'undefined' ? navigator : undefined;
    if (!nav?.mediaDevices?.getDisplayMedia) {
      return 'This browser cannot share tab audio. Use Chrome or Edge on a desktop computer, or paste the question manually.';
    }
    if (typeof window !== 'undefined' && window.isSecureContext === false) {
      return 'Tab audio sharing needs a secure address. Open the app on http://localhost:4200 (not a LAN IP) or use HTTPS.';
    }
    if (typeof MediaRecorder === 'undefined') {
      return 'This browser cannot record the shared audio. Use Chrome or Edge, or paste the question manually.';
    }
    return null;
  }

  get isCapturing(): boolean {
    return this.state$.value === 'capturing';
  }

  /** True while capturing and actually feeding STT (not paused). */
  private get listening(): boolean {
    return this.state$.value === 'capturing' && !this.paused$.value;
  }

  /**
   * Pause / resume STT without closing the meeting stream. The half-spoken
   * clip is discarded both ways, so no stale audio is sent after a resume.
   */
  setPaused(paused: boolean): void {
    if (!this.isCapturing || this.paused$.value === paused) return;
    this.liveOpen = false;
    this.liveTailMs = 0;
    this.livePreroll = [];
    this.silentTicks = 0;
    this._resetClipState();
    this.paused$.next(paused);
  }

  /**
   * Ask the user to share a tab / meeting window with AUDIO enabled.
   * Must be called from a user click (browsers require a gesture).
   *
   * Compatibility note: several browsers ignore `audio` when `video` is false.
   * So we first try audio-only, and if that comes back without an audio track we
   * retry with video enabled and immediately drop the video track.
   */
  async start(): Promise<void> {
    const blocked = this.getBlockReason();
    if (blocked) {
      this.status$.next(blocked);
      throw new Error('unsupported');
    }
    if (this.state$.value === 'capturing') return;

    this.state$.next('prompting');
    this.status$.next('Select meeting audio');

    const audioConstraints: MediaTrackConstraints = {
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
    };

    let stream: MediaStream | null = null;
    try {
      // Attempt 1: audio only (Chrome/Edge usually take the audio-only tab).
      try {
        stream = await navigator.mediaDevices.getDisplayMedia({
          video: false,
          audio: audioConstraints,
        });
      } catch (err) {
        // Some browsers throw NotSupportedError for audio-only sharing.
        if ((err as DOMException)?.name === 'NotAllowedError') throw err;
        stream = null;
      }

      // Attempt 2: allow video so the audio option becomes available.
      if (!stream || stream.getAudioTracks().length === 0) {
        stream?.getTracks().forEach((t) => t.stop());
        const fallback = await navigator.mediaDevices.getDisplayMedia({
          video: true,
          audio: audioConstraints,
        });
        // We only need the audio: release video immediately.
        fallback.getVideoTracks().forEach((t) => {
          t.stop();
          fallback.removeTrack(t);
        });
        stream = fallback;
      }
    } catch (err) {
      this.state$.next('idle');
      const name = (err as DOMException)?.name ?? '';
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        this.status$.next('Audio permission was denied');
      } else if (name === 'NotFoundError') {
        this.status$.next('No shared screen or tab was found.');
      } else {
        this.status$.next(
          'Browser audio capture is unavailable. Please paste the question manually.',
        );
      }
      throw err;
    }

    // The user can also stop sharing from the browser's own UI bar.
    const track = stream.getAudioTracks()[0];
    if (!track) {
      stream.getTracks().forEach((t) => t.stop());
      this.state$.next('idle');
      this.status$.next(
        'No audio track was shared. Pick the meeting tab again and tick "Also share tab audio".',
      );
      throw new Error('no audio track');
    }
    track.addEventListener('ended', () => void this.stop());

    this._beginCapture(stream);
  }

  /**
   * "Auto Select": capture the COMPUTER'S SOUND (system output) automatically,
   * with no browser "share a tab" picker.
   *
   * Browsers expose no direct "system audio" API, so this reads it through a
   * loopback INPUT device that mirrors the speakers — on Windows that is
   * "Stereo Mix" (enable it in Sound settings → Recording) or the free
   * VB-Cable virtual cable; on macOS BlackHole / Soundflower. Because that
   * device carries everything the system plays, the interviewer's voice is
   * heard even when the user wears headphones (the meeting audio plays through
   * the loopback). Users should close other media apps during the interview so
   * only the interviewer's voice reaches the AI.
   */
  async startSystemAudio(): Promise<boolean> {
    const blocked = this.getSystemAudioBlockReason();
    if (blocked) {
      this.status$.next(blocked);
      throw new Error('unsupported');
    }
    if (this.state$.value === 'capturing') return false;

    this.state$.next('prompting');
    this.status$.next('Detecting your computer\'s audio output…');
    this.systemAudioSetup$.next(null);

    const { deviceId, labels, denied } = await this._findSystemAudioDevice();

    // The user declined the microphone permission popup. Nothing is broken —
    // they just need to allow it so the browser can list audio devices.
    if (denied) {
      this.state$.next('idle');
      this.status$.next('Microphone permission was denied — allow it so Auto Select can list your devices.');
      throw new Error('permission denied');
    }

    // No loopback device exists yet. Don't throw (it's not an error the user
    // can fix in the app) — instead flag setup and let the UI guide them to
    // the exact Windows panel.
    if (!deviceId) {
      this.state$.next('idle');
      this.status$.next('Set up system audio, then click Auto Select again.');
      this.systemAudioSetup$.next(labels);
      return false;
    }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          deviceId: { exact: deviceId },
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });
    } catch (err) {
      this.state$.next('idle');
      const name = (err as DOMException)?.name ?? '';
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        this.status$.next('Audio permission was denied.');
      } else {
        this.status$.next('Could not open the system-audio device.');
      }
      throw err;
    }

    const track = stream.getAudioTracks()[0];
    if (!track) {
      stream.getTracks().forEach((t) => t.stop());
      this.state$.next('idle');
      this.status$.next('The system-audio device returned no audio track.');
      throw new Error('no audio track');
    }
    track.addEventListener('ended', () => void this.stop());

    this._beginCapture(stream);
    return true;
  }

  /**
   * Desktop (.exe) "Auto Select": the Electron main process answers
   * getDisplayMedia() itself with Windows loopback audio — no picker, no
   * Stereo Mix. Resolves false (state back to idle) when no audio came back,
   * so the caller can fall back to a loopback input device.
   */
  async startDesktopLoopbackAudio(): Promise<boolean> {
    if (this.state$.value === 'capturing') return false;
    if (!navigator.mediaDevices?.getDisplayMedia) return false;

    this.state$.next('prompting');
    this.status$.next('Connecting to your computer\'s sound…');
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
    } catch {
      this.state$.next('idle');
      return false;
    }
    // Only the audio is needed: release the screen video immediately.
    stream.getVideoTracks().forEach((t) => {
      t.stop();
      stream.removeTrack(t);
    });
    const track = stream.getAudioTracks()[0];
    if (!track) {
      stream.getTracks().forEach((t) => t.stop());
      this.state$.next('idle');
      return false;
    }
    track.addEventListener('ended', () => void this.stop());

    this.systemAudioSetup$.next(null);
    this._beginCapture(stream);
    return true;
  }

  /** Why "Auto Select" (system audio) cannot be used, or null when supported. */
  getSystemAudioBlockReason(): string | null {
    const nav = typeof navigator !== 'undefined' ? navigator : undefined;
    if (!nav?.mediaDevices?.getUserMedia) {
      return 'This browser cannot capture system audio. Use Chrome or Edge on a desktop computer.';
    }
    if (typeof window !== 'undefined' && window.isSecureContext === false) {
      return 'System-audio capture needs a secure address. Open the app on http://localhost:4200 (not a LAN IP) or use HTTPS.';
    }
    return null;
  }

  /** Stop capture and flush whatever was recorded. */
  async stop(): Promise<void> {
    // Already idle (or never started): make sure the UI is not left showing
    // "Stop interviewer audio" and tear down any lingering resources.
    if (!this.recorder || this.state$.value !== 'capturing') {
      this.state$.next('idle');
      this._cleanup();
      return;
    }
    if (this.stopPromise) return this.stopPromise;

    this.state$.next('stopping');
    this.stopPromise = new Promise<void>((resolve) => {
      const recorder = this.recorder;
      if (!recorder) {
        this.state$.next('idle');
        this._cleanup();
        this.stopPromise = null;
        resolve();
        return;
      }

      const finish = () => {
        this.status$.next('Interviewer audio capture stopped.');
        this.state$.next('idle');
        this._cleanup();
        this.stopPromise = null;
        resolve();
      };

      // If the recorder is already inactive the 'stop' event will never fire,
      // so finish immediately instead of leaving the UI stuck on "stopping".
      if (recorder.state === 'inactive') {
        finish();
        return;
      }

      recorder.addEventListener('stop', finish, { once: true });
      try {
        recorder.stop();
      } catch {
        finish();
      }
    });
    return this.stopPromise;
  }

  // ---------------------------------------------------------------- internals

  /**
   * Common capture setup once a stream has been obtained (whether from the tab
   * picker or from a system-audio loopback device). Both sources flow through
   * the identical PCM -> silence-detection -> WAV -> STT pipeline.
   */
  private _beginCapture(stream: MediaStream): void {
    this.stream = stream;
    this.systemAudioSetup$.next(null);
    this._setupRecorder();
    this._setupCaptureGraph(stream);
    this.state$.next('capturing');
    this.status$.next("Listening for the interviewer's question…");
  }

  /**
   * Find the input device that mirrors the computer's sound output.
   *
   * Device labels are hidden until the site holds media permission, so when the
   * labels come back empty we request (and immediately release) a throwaway mic
   * stream purely to unlock them, then enumerate again.
   */
  private async _findSystemAudioDevice(): Promise<{
    deviceId: string | null;
    labels: string[];
    denied: boolean;
  }> {
    const md = navigator.mediaDevices;
    if (!md?.enumerateDevices) return { deviceId: null, labels: [], denied: false };

    let devices: MediaDeviceInfo[] = [];
    try {
      devices = await md.enumerateDevices();
    } catch {
      return { deviceId: null, labels: [], denied: false };
    }

    const labelsVisible = devices.some((d) => d.kind === 'audioinput' && !!d.label);
    if (!labelsVisible) {
      try {
        const probe = await md.getUserMedia({ audio: true });
        probe.getTracks().forEach((t) => t.stop());
        devices = await md.enumerateDevices();
      } catch (err) {
        const denied = (err as DOMException)?.name === 'NotAllowedError';
        return { deviceId: null, labels: [], denied };
      }
    }

    const inputs = devices.filter((d) => d.kind === 'audioinput');
    const labels = inputs.map((i) => i.label).filter(Boolean);
    for (const d of inputs) {
      if (!d.label) continue;
      const label = d.label.toLowerCase();
      if (SYSTEM_AUDIO_LABELS.some((token) => label.includes(token))) {
        return { deviceId: d.deviceId, labels, denied: false };
      }
    }
    return { deviceId: null, labels, denied: false };
  }

  private pickMimeType(): string {
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/ogg;codecs=opus',
      'audio/mp4',
    ];
    for (const type of candidates) {
      if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(type)) {
        return type;
      }
    }
    return '';
  }

  /**
   * Start ONE recorder that runs for the whole capture session.
   *
   * The previous version stopped/started the recorder on every pause. That was
   * racy: the outgoing recorder's final `dataavailable` fragment arrived AFTER
   * a new recorder had been created, so Whisper sometimes received a WebM
   * fragment with no header -> HTTP 400 "could not process file". It also sent
   * two clips per question, which doubled the request rate and caused 429s.
   *
   * Now the recorder is never interrupted. Clips are cut from the accumulated
   * chunk list, always starting at chunk 0 (which carries the container header),
   * so every clip is a valid, self-contained audio file.
   */
  private _setupRecorder(): void {
    if (!this.stream) return;
    const mimeType = this.pickMimeType();
    try {
      this.recorder = new MediaRecorder(this.stream, mimeType ? { mimeType } : undefined);
    } catch {
      this.recorder = new MediaRecorder(this.stream);
    }

    this.recordedMime = this.recorder.mimeType || mimeType || 'audio/webm';
    this.recorder.ondataavailable = (ev) => {
      if (ev.data && ev.data.size > 0 && !this.paused$.value) {
        this.pendingChunks.push(ev.data);
        this.pendingCount += 1;
      }
    };
    this.recorder.onstop = () => void this._flushRemaining();
    this.recorder.start(CHUNK_MS);
    this.clipStartedAt = Date.now();
  }

  /**
   * Emit the accumulated audio as 16 kHz mono WAV.
   *
   * `AudioContext` capture is already pumping raw PCM, so we build the WAV from
   * that directly — no container, no decoder, nothing for the provider to
   * reject. Only if that path is unavailable do we decode MediaRecorder output,
   * and if even that fails the original bytes are sent rather than losing the
   * question.
   */
  private async _flushRemaining(): Promise<void> {
    await this._cutClip();
  }

  private async _emitBlob(blob: Blob): Promise<void> {
    const raw = await blob.arrayBuffer();
    const wav = await toWav(raw);
    if (!wav) {
      // A decode failure would otherwise surface later as an opaque provider
      // error. Say it in plain English while capture is still running.
      console.warn('[interviewer-audio] decodeAudioData failed; sending raw clip', raw.byteLength);
      this.status$.next('Transcription format check failed. The next question may not be heard.');
      setTimeout(() => {
        if (this.isCapturing) this.status$.next("Listening for the interviewer's question...");
      }, 5000);
    }
    this.clip$.next(
      wav ? { data: wav, mimeType: 'audio/wav' } : { data: raw, mimeType: this.recordedMime },
    );
  }

  private _resetClipState(): void {
    this.pendingChunks = [];
    this.pendingCount = 0;
    this.pcmChunks = [];
    this.pcmCount = 0;
    this.silentMs = 0;
    this.clipHasSpeech = false;
    this.voicedMs = 0;
    this.lastPreviewMs = 0;
    this.clipStartedAt = Date.now();
  }

  /**
   * Build the capture graph: an analyser (cheap level meter) plus a
   * ScriptProcessor that hands us raw PCM samples.
   *
   * This is the whole point of the design: once we hold samples, transcription
   * input is plain WAV, so MediaRecorder container quirks, mime labels and
   * decoder failures cannot break it.
   */
  private _setupCaptureGraph(stream: MediaStream): void {
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) {
      // No context: rely on MediaRecorder output alone.
      this.analyser = null;
      this.hasPcmCapture = false;
      return;
    }

    try {
      this.audioCtx = new Ctor();
      void this.audioCtx.resume().catch(() => undefined);
    } catch {
      this.audioCtx = null;
      this.analyser = null;
      this.hasPcmCapture = false;
      return;
    }

    const source = this.audioCtx.createMediaStreamSource(stream);

    try {
      this.analyser = this.audioCtx.createAnalyser();
      this.analyser.fftSize = 512;
      source.connect(this.analyser);
    } catch {
      this.analyser = null;
    }

    try {
      const processor = this.audioCtx.createScriptProcessor(PCM_BUFFER_SIZE, 1, 1);
      processor.onaudioprocess = (ev) => {
        if (!this.listening) return;
        const input = ev.inputBuffer.getChannelData(0);
        // The underlying buffer is reused by the audio thread - copy it.
        const samples = Float32Array.from(input);
        this.pcmChunks.push(samples);
        this.pcmCount += input.length;
        this.pcmRate = ev.inputBuffer.sampleRate || this.audioCtx!.sampleRate;
        const ms = (input.length / this.pcmRate) * 1000;
        const voiced = this._trackLevel(input, ms);
        this._streamLive(samples, ms, voiced);
      };
      source.connect(processor);
      // A script processor only runs while it reaches the destination. Route it
      // through a muted gain so the meeting is not played back a second time.
      const sink = this.audioCtx.createGain();
      sink.gain.value = 0;
      processor.connect(sink);
      sink.connect(this.audioCtx.destination);
      this.pcmProcessor = processor;
      this.hasPcmCapture = true;
    } catch (err) {
      console.warn('[interviewer-audio] PCM capture unavailable, falling back', err);
      this.hasPcmCapture = false;
    }

    if (this.hasPcmCapture) return;

    // Fallback: no PCM path, so measure level with the analyser instead.
    const buf = new Uint8Array(this.analyser ? this.analyser.frequencyBinCount : 0);
    this.energyTimer = setInterval(() => {
      if (!this.listening) return;
      let rms = 0;
      if (this.analyser) {
        this.analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        rms = Math.sqrt(sum / buf.length);
      }
      if (rms < this.silenceThreshold) {
        this.silentTicks++;
        if (this.silentTicks >= this.silenceTicksToCut) {
          this.silentTicks = 0;
          if (this.clipHasSpeech) void this._cutClip();
        }
      } else {
        this.silentTicks = 0;
        this.clipHasSpeech = true;
        this.voicedMs += 100;
        if (this.pendingCount * CHUNK_MS >= MAX_CLIP_MS) void this._cutClip();
      }
    }, 100);
  }

  /**
   * Speech/silence bookkeeping, driven by the real PCM samples.
   *
   * `silentMs` is what ends a clip: when the interviewer stops talking for a
   * moment we send what we have instead of one huge meeting recording.
   */
  private _trackLevel(samples: Float32Array, durationMs: number): boolean {
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    const rms = Math.sqrt(sum / Math.max(1, samples.length));
    const voiced = rms >= this.silenceThreshold;

    if (voiced) {
      this.clipHasSpeech = true;
      this.voicedMs += durationMs;
      this.silentMs = 0;
    } else {
      const wasSilentMs = this.silentMs;
      this.silentMs += durationMs;
      if (
        this.liveAnswers &&
        this.clipHasSpeech &&
        this.voicedMs >= MIN_VOICED_MS &&
        wasSilentMs < PAUSE_SIGNAL_MS &&
        this.silentMs >= PAUSE_SIGNAL_MS
      ) {
        this.pause$.next();
      }
      // Only quiet frames teach us the noise floor: it falls fast, rises slowly.
      this.noiseFloor += (rms - this.noiseFloor) * (rms < this.noiseFloor ? 0.5 : 0.03);
      // A fixed threshold fails both ways: a hot loopback hum never reads as
      // silence (clips only end at the 15 s cap), a quiet tab never reads as
      // speech. Track the floor instead, within sane bounds.
      this.silenceThreshold = Math.min(
        MAX_SILENCE_THRESHOLD,
        Math.max(MIN_SILENCE_THRESHOLD, this.noiseFloor * NOISE_MARGIN),
      );
    }

    const clipMs = this.pcmCount ? (this.pcmCount / (this.pcmRate || 1)) * 1000 : 0;
    if (clipMs >= MIN_CLIP_MS && this.silentMs >= SILENCE_MS_TO_CUT) {
      void this._cutClip();
    } else if (clipMs >= MAX_CLIP_MS) {
      // Safety valve for uninterrupted talking.
      void this._cutClip();
    } else {
      this._maybePreview(clipMs);
    }
    return voiced;
  }

  /**
   * Feed the live transcript stream. Speech opens it (flushing ~250 ms of
   * pre-roll so the first word is not clipped); after the last voiced frame a
   * LIVE_TAIL_MS tail of real quiet is still sent so the provider can close
   * the turn, then it stops — silence is never streamed (or billed).
   */
  private _streamLive(samples: Float32Array, durationMs: number, voiced: boolean): void {
    if (!this.liveStream || !this.pcmRate) return;
    if (voiced) {
      if (!this.liveOpen) {
        this.liveOpen = true;
        for (const f of this.livePreroll) this._sendLive(f);
        this.livePreroll = [];
      }
      this.liveTailMs = LIVE_TAIL_MS;
      this._sendLive(samples);
    } else if (this.liveOpen) {
      this._sendLive(samples);
      this.liveTailMs -= durationMs;
      if (this.liveTailMs <= 0) this.liveOpen = false;
    } else {
      this.livePreroll.push(samples);
      if (this.livePreroll.length > LIVE_PREROLL_FRAMES) this.livePreroll.shift();
    }
  }

  private _sendLive(samples: Float32Array): void {
    const frame = toPcm16(samples, this.pcmRate, LIVE_PCM_TAG.length);
    new Uint8Array(frame).set(LIVE_PCM_TAG, 0);
    this.livePcm$.next(frame);
  }

  /**
   * Snapshot the clip so far for the live transcript while the interviewer is
   * mid-sentence. Only real speech (same voiced gate as a clip) and only new
   * audio: a pause sends nothing, since the last snapshot already covers it.
   */
  private _maybePreview(clipMs: number): void {
    if (!this.livePreview || this.cutting || !this.pcmRate) return;
    if (!this.clipHasSpeech || this.voicedMs < MIN_VOICED_MS) return;
    if (this.silentMs >= PREVIEW_QUIET_MS) return;
    if (clipMs - this.lastPreviewMs < PREVIEW_INTERVAL_MS) return;
    this.lastPreviewMs = clipMs;
    const wav = pcmToWav(this.pcmChunks, this.pcmRate, undefined, this.silenceThreshold);
    if (wav.byteLength > 44) this.preview$.next({ data: wav, mimeType: 'audio/wav' });
  }

  /** Hand the current clip to the WebSocket, then start a fresh one. */
  private async _cutClip(): Promise<void> {
    if (this.cutting) return;
    this.cutting = true;
    try {
      // A click, cough or keyboard burst also crosses the threshold, but a
      // question needs real voiced time. Such clips came back as 0 chars after
      // 2–2.6s of STT and held the single in-flight slot, so the NEXT (real)
      // question waited behind them. Drop them here instead.
      const hasSpeech = this.clipHasSpeech && this.voicedMs >= MIN_VOICED_MS;
      const pcmChunks = this.pcmChunks;
      const pcmCount = this.pcmCount;
      const pcmRate = this.pcmRate;
      const recorderChunks = this.pendingChunks;
      // Reset immediately: capture continues while we encode + upload.
      this._resetClipState();

      // Never upload silence or noise - it wastes STT time, money and quota.
      if (!hasSpeech) return;

      // Primary path: encode the PCM we already hold. No decoder involved.
      if (pcmCount > 0 && pcmRate > 0) {
        const wav = pcmToWav(pcmChunks, pcmRate, undefined, this.silenceThreshold);
        if (wav.byteLength > 44) {
          this.clip$.next({ data: wav, mimeType: 'audio/wav' });
          return;
        }
      }

      // Fallback: decode whatever MediaRecorder produced.
      if (recorderChunks.length) {
        await this._emitBlob(new Blob(recorderChunks, { type: this.recordedMime }));
      }
    } finally {
      this.cutting = false;
    }
  }

  private _cleanup(): void {
    if (this.energyTimer) {
      clearInterval(this.energyTimer);
      this.energyTimer = null;
    }
    // Disconnect the PCM graph before closing, so no callback runs on a dead
    // context. (`onaudioprocess` is also guarded by the capturing state.)
    if (this.pcmProcessor) {
      this.pcmProcessor.onaudioprocess = null;
      try {
        this.pcmProcessor.disconnect();
      } catch {
        /* already torn down */
      }
      this.pcmProcessor = null;
    }
    this.hasPcmCapture = false;
    this.analyser = null;
    this.audioCtx?.close().catch(() => undefined);
    this.audioCtx = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.recorder = null;
    this.silentTicks = 0;
    this.liveOpen = false;
    this.liveTailMs = 0;
    this.livePreroll = [];
    if (this.paused$.value) this.paused$.next(false);
    this.silenceThreshold = DEFAULT_SILENCE_THRESHOLD;
    this.noiseFloor = DEFAULT_SILENCE_THRESHOLD / NOISE_MARGIN;
    this._resetClipState();
  }
}

/** MediaRecorder chunk size; also our timing unit for fallback clip length. */
const CHUNK_MS = 250;
/** Don't send clips shorter than this (too little context to transcribe). */
const MIN_CLIP_MS = 1_500;
/**
 * Minimum voiced audio for a clip to be transcribed. The backend only answers
 * 3+ words (or 25+ chars), which is well over 0.5s of speech, so shorter
 * voiced time is noise (click, cough, keyboard), never a question.
 */
export const MIN_VOICED_MS = 400;
/**
 * Live transcript cadence: one snapshot per second of clip audio, so the
 * Transcript panel trails the interviewer by ~1s + STT latency instead of
 * waiting for the whole sentence plus the silence cut.
 */
export const PREVIEW_INTERVAL_MS = 1_000;
/** No snapshot once the speaker has been quiet this long (nothing new to show). */
const PREVIEW_QUIET_MS = 300;
/** Tag on every live-stream frame (backend: live_stream.LIVE_PCM_TAG). */
export const LIVE_PCM_TAG = new Uint8Array([0x4c, 0x50, 0x43, 0x4d]); // "LPCM"
/**
 * Quiet still streamed after speech. Gemini Live closed its turn ~1.3 s after
 * the last word in measurements, so the tail must cover that or the final
 * text for the turn never arrives.
 */
export const LIVE_TAIL_MS = 1_600;
/** Frames kept from before speech onset (~85 ms each at 48 kHz). */
const LIVE_PREROLL_FRAMES = 3;
/** Hard cap per clip so one long answer still reaches the AI. */
const MAX_CLIP_MS = 15_000;
/**
 * Quiet for this long ends the clip (the interviewer has finished a sentence).
 * 0.85s sits above phrase-internal pauses (~200–500ms) and shaves 150ms of dead
 * air off every question; a clip that still lands mid-question is stitched by
 * the backend QuestionBuffer hold window.
 */
const SILENCE_MS_TO_CUT = 850;
/**
 * Quiet that counts as "the interviewer paused" for live answering. Human
 * turn gaps are ~200–500 ms; the backend only answers on it when the live
 * sentence is already closed ("…a deadlock?"), so a mid-question breath of
 * this length cannot answer a fragment.
 */
export const PAUSE_SIGNAL_MS = 450;
/** Adaptive silence threshold: starting point, bounds, and margin over the floor. */
const DEFAULT_SILENCE_THRESHOLD = 0.012;
const MIN_SILENCE_THRESHOLD = 0.006;
const MAX_SILENCE_THRESHOLD = 0.035;
const NOISE_MARGIN = 3;
/** ScriptProcessor buffer; ~85 ms of samples at 48 kHz. */
const PCM_BUFFER_SIZE = 4_096;

/**
 * Device-label fragments that identify a system-audio loopback input across
 * platforms. "Auto Select" matches an input device whose label contains any of
 * these (case-insensitive): Stereo Mix / What U Hear (Windows), VB-Cable /
 * Voicemeeter (virtual cable), BlackHole / Soundflower (macOS), monitor sources
 * (Linux PulseAudio/PipeWire).
 */
const SYSTEM_AUDIO_LABELS = [
  'stereo mix',
  'stereomix',
  'stereo-mix',
  'mezcla estéreo',
  'mixage stéréo',
  'missaggio stereo',
  'what u hear',
  'what-u-hear',
  'wave out',
  'loopback',
  'cable output',
  'vb-audio',
  'virtual cable',
  'voicemeeter',
  'virtual audio',
  'system sound',
  'monitor of',
  'rec. playback',
  'blackhole',
  'soundflower',
];
