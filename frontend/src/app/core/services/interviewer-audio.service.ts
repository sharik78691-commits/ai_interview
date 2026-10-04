import { Injectable } from '@angular/core';
import { BehaviorSubject, Subject } from 'rxjs';
import { pcmToWav, toWav } from './wav-encoder';

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
  private cutting = false;
  private recordedMime = 'audio/webm';

  /** Emitted audio clips: raw bytes + mime type, ready for the WebSocket. */
  readonly clip$ = new Subject<{ data: ArrayBuffer; mimeType: string }>();
  /** User-facing status message for the UI. */
  readonly status$ = new BehaviorSubject<string>('');

  /** Consecutive silent checks (~100ms each) that end a clip. */
  private readonly silenceTicksToCut = 12;
  /** RMS below this counts as silence. */
  private readonly silenceThreshold = 0.012;

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

    this.stream = stream;
    this._setupRecorder();
    this._setupCaptureGraph(stream);
    this.state$.next('capturing');
    this.status$.next("Listening for the interviewer's question...");
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
      if (ev.data && ev.data.size > 0) {
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
        if (this.state$.value !== 'capturing') return;
        const input = ev.inputBuffer.getChannelData(0);
        // The underlying buffer is reused by the audio thread - copy it.
        this.pcmChunks.push(Float32Array.from(input));
        this.pcmCount += input.length;
        this.pcmRate = ev.inputBuffer.sampleRate || this.audioCtx!.sampleRate;
        this._trackLevel(input, (input.length / this.pcmRate) * 1000);
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
      if (this.state$.value !== 'capturing') return;
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
  private _trackLevel(samples: Float32Array, durationMs: number): void {
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    const rms = Math.sqrt(sum / Math.max(1, samples.length));

    if (rms >= this.silenceThreshold) {
      this.clipHasSpeech = true;
      this.silentMs = 0;
    } else {
      this.silentMs += durationMs;
    }

    const clipMs = this.pcmCount ? (this.pcmCount / (this.pcmRate || 1)) * 1000 : 0;
    if (clipMs >= MIN_CLIP_MS && this.silentMs >= SILENCE_MS_TO_CUT) {
      void this._cutClip();
    } else if (clipMs >= MAX_CLIP_MS) {
      // Safety valve for uninterrupted talking.
      void this._cutClip();
    }
  }

  /** Hand the current clip to the WebSocket, then start a fresh one. */
  private async _cutClip(): Promise<void> {
    if (this.cutting) return;
    this.cutting = true;
    try {
      const hasSpeech = this.clipHasSpeech;
      const pcmChunks = this.pcmChunks;
      const pcmCount = this.pcmCount;
      const pcmRate = this.pcmRate;
      const recorderChunks = this.pendingChunks;
      // Reset immediately: capture continues while we encode + upload.
      this._resetClipState();

      // Never upload silence - it only wastes rate limit quota.
      if (!hasSpeech) return;

      // Primary path: encode the PCM we already hold. No decoder involved.
      if (pcmCount > 0 && pcmRate > 0) {
        const wav = pcmToWav(pcmChunks, pcmRate);
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
    this._resetClipState();
  }
}

/** MediaRecorder chunk size; also our timing unit for fallback clip length. */
const CHUNK_MS = 250;
/** Don't send clips shorter than this (too little context to transcribe). */
const MIN_CLIP_MS = 1_500;
/** Hard cap per clip so one long answer still reaches the AI. */
const MAX_CLIP_MS = 15_000;
/** Quiet for this long ends the clip (the interviewer has finished a sentence). */
const SILENCE_MS_TO_CUT = 1_200;
/** ScriptProcessor buffer; ~85 ms of samples at 48 kHz. */
const PCM_BUFFER_SIZE = 4_096;
