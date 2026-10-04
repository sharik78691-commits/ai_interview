import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import {
  AIInterviewResponse,
  HistoryItem,
  InterviewStatus,
  RESPONSE_LENGTH_LABELS,
  ResponseLength,
  TranscriptEntry,
} from '../../core/models/interview.models';
import { FONT_SIZE_OPTIONS, MAX_FONT_SIZE, MIN_FONT_SIZE } from '../../core/services/settings.service';
import { DemoService } from '../../core/services/demo.service';
import { InterviewerAudioService } from '../../core/services/interviewer-audio.service';
import { InterviewWsService } from '../../core/services/interview-ws.service';
import { QuestionDetectorService } from '../../core/services/question-detector.service';
import { ResumeService } from '../../core/services/resume.service';
import { SettingsService } from '../../core/services/settings.service';
import { TranscriptionService } from '../../core/services/transcription.service';
import { ErrorBannerComponent } from '../../shared/components/error-banner/error-banner.component';
import { TimeFormatPipe } from '../../shared/pipes/time-format.pipe';

@Component({
  selector: 'app-interview',
  standalone: true,
  imports: [FormsModule, ErrorBannerComponent, TimeFormatPipe],
  templateUrl: './interview.component.html',
  styleUrl: './interview.component.css',
})
export class InterviewComponent implements OnInit, OnDestroy {
  private transcription = inject(TranscriptionService);
  private detector = inject(QuestionDetectorService);
  private ws = inject(InterviewWsService);
  private resumeService = inject(ResumeService);
  /** True when the backend has a speech-to-text provider for meeting audio. */
  sttReady = false;
  private demo = inject(DemoService);
  private settings = inject(SettingsService);

  entries: TranscriptEntry[] = [];
  interim = '';
  status: InterviewStatus = 'idle';
  guidance: AIInterviewResponse | null = null;
  history: HistoryItem[] = [];
  notice = '';
  flash = false;
  error = '';
  micSupported = true;
  /** Answer depth the user picked for the next question. */
  responseLength: ResponseLength = 'medium';
  readonly lengthOptions = Object.entries(RESPONSE_LENGTH_LABELS) as Array<[
    ResponseLength,
    string,
  ]>;
  /** Loading indicator while the AI composes a long answer. */
  thinking = false;

  // ------------------------------------------------------- guidance text size
  /** AI Guidance body text size in px (user-adjustable, persisted). */
  guidanceFontSize = 15;
  readonly fontSizeOptions = FONT_SIZE_OPTIONS;
  readonly minFontSize = MIN_FONT_SIZE;
  readonly maxFontSize = MAX_FONT_SIZE;

  // ---------------------------------------------------------------- interviewer audio
  interviewerAudio = inject(InterviewerAudioService);
  /** Latest status message for the interviewer-audio feature. */
  audioStatus = '';
  /** True while tab/meeting audio is being captured. */
  capturingInterviewer = false;
  /** Text typed or pasted by the user (manual fallback). */
  manualQuestion = '';
  /** Copy the generated answer to the clipboard. */
  copied = false;
  /** Copy the interviewer transcript to the clipboard. */
  copiedTranscript = false;
  /** Last interviewer question recognised from meeting/tab audio. */
  lastInterviewerText = '';
  /** Scope of the current notice: AI fallback vs. speech-to-text problem. */
  noticeScope: 'ai' | 'stt' = 'ai';
  /** One transcription request is in flight; extra clips wait their turn. */
  audioBusy = false;
  private clipQueue: { data: ArrayBuffer; mimeType: string }[] = [];

  /** Answer text used by the "Copy Answer" button. */
  get answerText(): string {
    const g = this.guidance;
    if (!g) return '';
    const parts: string[] = [`Question: ${g.question}`, '', 'Answer points:'];
    g.answerPoints.forEach((p, i) => parts.push(`${i + 1}. ${p}`));
    if (g.keyTakeaways?.length) {
      parts.push('', 'Key takeaways:');
      g.keyTakeaways.forEach((t) => parts.push(`- ${t}`));
    }
    if (g.example) parts.push('', `Example: ${g.example}`);
    if (g.star) {
      parts.push('', 'STAR:');
      if (typeof g.star === 'string') parts.push(g.star);
      else Object.entries(g.star).forEach(([k, v]) => parts.push(`${k}: ${v}`));
    }
    if (g.codeHint) parts.push('', `Code hint:\n${g.codeHint}`);
    if (g.followUpQuestions?.length) {
      parts.push('', 'Follow-up questions:');
      g.followUpQuestions.forEach((f) => parts.push(`- ${f}`));
    }
    return parts.join('\n');
  }
  elapsed = 0;
  live = false;
  private timer: ReturnType<typeof setInterval> | null = null;
  private subs: Subscription[] = [];

  ngOnInit(): void {
    this.responseLength = this.settings.value.responseLength;
    this.guidanceFontSize = this.settings.value.guidanceFontSize;
    this.ws.connect();
    this.transcription.connect();
    this.micSupported = this.transcription.isSupported();
    // Meeting audio needs server-side transcription; show the state up front.
    this.resumeService.checkHealth().subscribe({
      next: (h) => (this.sttReady = h.stt_configured !== false),
      error: () => (this.sttReady = false),
    });

    this.subs.push(
      this.transcription.transcript$.subscribe((t) => this.onTranscript(t.text, t.isFinal)),
      this.transcription.interim$.subscribe((s) => (this.interim = s)),
      this.detector.completeQuestions$.subscribe((q) => this.onQuestion(q)),
      this.ws.guidance$.subscribe((g) => {
        if (g) {
          this.guidance = g;
          this.thinking = false;
          this.flash = false;
          requestAnimationFrame(() => (this.flash = true));
        }
      }),
      this.ws.status$.subscribe((s) => (this.status = s)),
      this.ws.history$.subscribe((h) => (this.history = h)),
      this.ws.notice$.subscribe(({ message, scope }) => {
        this.notice = message;
        this.noticeScope = scope;
        // A speech-to-text failure means no answer is coming — clear the spinner.
        if (scope === 'stt') {
          this.thinking = false;
          this.releaseClipSlot();
        }
        // Auto-dismiss so a transient limit doesn't stick around forever.
        setTimeout(() => (this.notice = ''), 12000);
      }),

      // --- Interviewer (meeting/tab) audio: clips -> backend STT -> existing AI flow ---
      // "Audio permission was denied" already appears in the top error banner
      // (set by startInterviewerAudio), so it is not repeated inside this block.
      this.interviewerAudio.status$.subscribe((s) => {
        if (s === 'Audio permission was denied') return;
        this.audioStatus = s;
      }),
      this.interviewerAudio.state$.subscribe((s) => {
        this.capturingInterviewer = s === 'capturing';
        if (s === 'idle') {
          this.clipQueue = [];
          this.audioBusy = false;
        }
      }),
      // One clip in flight at a time. Sending every clip immediately used to
      // hit the transcription rate limit during a fast interview.
      this.interviewerAudio.clip$.subscribe((clip) => this.enqueueClip(clip)),
      // Server-side STT result: show it, and let the backend's own question
      // detection decide when to call the AI (no extra AI call here).
      this.ws.interviewerTranscript$.subscribe(({ text }) => {
        this.lastInterviewerText = text;
        // Also record it in the Transcript panel so the meeting audio shows up
        // alongside microphone speech (previously only "Heard:" updated).
        const clean = text.trim();
        if (clean) {
          this.entries.push({
            speaker: 'interviewer',
            text: clean,
            timestamp: new Date(),
            isQuestion: QuestionDetectorService.detect(clean),
          });
        }
        this.releaseClipSlot();
      }),
      this.ws.messages$.subscribe((m) => {
        if (m['type'] === 'status') {
          const msg = String(m['message'] ?? '');
          if (/Question detected/i.test(msg)) {
            // Backend accepted the utterance and started the AI.
            this.audioStatus = 'Generating answer…';
            this.thinking = true;
          } else if (/no speech|waiting for a complete/i.test(msg)) {
            // Backchannel or silence — back to listening, spinner off.
            this.audioStatus = "Listening for the interviewer's question…";
            this.thinking = false;
          } else if (/Generating|processing/i.test(msg)) {
            this.audioStatus = 'Generating answer…';
          } else if (/listening|connected|reset/i.test(msg)) {
            this.audioStatus = "Listening for the interviewer's question…";
          }
        }
        if (m['type'] === 'ai_guidance') {
          this.audioStatus = 'Answer ready';
          this.thinking = false;
        }
        // A failed transcription also frees the slot so the queue keeps moving.
        if (m['type'] === 'error') {
          this.releaseClipSlot();
          this.thinking = false;
        }
      }),
    );
  }

  /**
   * Queue one interviewer clip. Only the newest clip is kept when the network
   * is behind, because an older question is already out of date.
   */
  private enqueueClip(clip: { data: ArrayBuffer; mimeType: string }): void {
    if (this.clipQueue.length) this.clipQueue = [clip];
    else this.clipQueue.push(clip);
    this.pumpClipQueue();
  }

  private pumpClipQueue(): void {
    if (this.audioBusy || !this.clipQueue.length) return;
    if (!this.ws.isConnected()) return; // keep it queued until the socket is back
    const clip = this.clipQueue.shift()!;
    this.audioBusy = true;
    // Transcription first, AI second — do not claim "Question detected" yet.
    this.audioStatus = 'Transcribing interviewer audio…';
    this.thinking = true;
    this.ws.sendAudioClip(clip.data, clip.mimeType, this.responseLength);
    // Safety valve: never stall the queue if the backend stays silent.
    setTimeout(() => this.releaseClipSlot(), 10000);
  }

  private releaseClipSlot(): void {
    this.audioBusy = false;
    this.pumpClipQueue();
  }

  /** Past Q&As excluding the one shown in the current-guidance card. */
  get pastHistory(): HistoryItem[] {
    if (this.history.length <= 1) return [];
    return this.history.slice(0, -1).reverse();
  }

  /** Only the latest two transcript entries are shown in the Transcript panel. */
  get recentEntries(): TranscriptEntry[] {
    return this.entries.slice(-2);
  }

  ngOnDestroy(): void {
    this.subs.forEach((s) => s.unsubscribe());
    this.stopTimer();
    this.transcription.stop();
    void this.interviewerAudio.stop();
    this.ws.disconnect();
  }

  get statusLabel(): string {
    if (this.thinking || this.audioStatus === 'Generating answer…') return 'AI Thinking';
    switch (this.status) {
      case 'listening': return 'Listening';
      case 'processing': return 'Processing';
      case 'thinking': return 'AI Thinking';
      case 'ready': return 'Ready';
      case 'paused': return 'Paused';
      case 'error': return 'Connection issue';
      default: return this.live ? 'Live' : 'Idle';
    }
  }

  /** Rough word budget shown next to the depth selector. */
  get lengthHint(): string {
    switch (this.responseLength) {
      case 'short': return '3–4 quick bullets';
      case 'long': return '8–12 detailed bullets + example';
      default: return '5–7 structured bullets';
    }
  }

  runDemo(): void {
    this.error = '';
    this.live = true;
    this.ws.setStatus('listening');
    this.startTimer();
    this.demo.runDemoSequence({
      onTranscript: (t) => this.onTranscript(t, true),
      onGuidance: (g) => this.ws.pushLocalGuidance(g),
      onDone: () => {
        this.ws.setStatus('ready');
        this.live = false;
      },
    });
  }

  // ------------------------------------------------ interviewer audio controls

  /** Ask for tab/meeting audio permission and start capturing the interviewer. */
  async startInterviewerAudio(): Promise<void> {
    this.error = '';
    try {
      await this.interviewerAudio.start();
      this.ws.sendInterviewerAudioStart('audio/wav');
    } catch {
      // Status + user-facing error already set by the service.
      this.error = this.interviewerAudio.status$.value;
    }
  }

  /** Exact reason tab/meeting audio cannot be used (null when supported). */
  get captureBlockReason(): string | null {
    return this.interviewerAudio.getBlockReason();
  }

  /** Stop capturing interviewer audio. */
  async stopInterviewerAudio(): Promise<void> {
    await this.interviewerAudio.stop();
    this.ws.sendInterviewerAudioStop();
  }

  // ------------------------------------------------------- manual fallback

  /**
   * Send the typed / pasted interviewer question to the EXISTING AI answer
   * feature (same WebSocket message, same answer card).
   */
  generateAnswer(): void {
    const question = this.manualQuestion.trim();
    if (!question) {
      this.audioStatus = 'Browser audio capture is unavailable. Please paste the question manually.';
      return;
    }
    this.audioStatus = 'Generating answer…';
    this.thinking = true;
    this.lastInterviewerText = question;
    if (this.ws.isConnected()) {
      this.ws.sendInterviewerQuestion(question, this.responseLength);
    } else {
      this.resumeService.analyzeQuestion(question, this.responseLength).subscribe({
        next: (g) => {
          this.thinking = false;
          this.audioStatus = 'Answer ready';
          this.ws.pushLocalGuidance({ ...g, question });
        },
        error: () => {
          this.audioStatus = 'Answer ready';
          this.ws.pushLocalGuidance(this.demo.guidanceFor(question));
        },
      });
    }
  }

  /**
   * Submit the manual question on Enter, but allow Shift+Enter to insert a
   * newline so the two-line box can hold multi-line questions.
   */
  onManualEnter(event: Event): void {
    const ke = event as KeyboardEvent;
    if (ke.shiftKey) {
      return;
    }
    ke.preventDefault();
    this.generateAnswer();
  }

  /** Clear the manual question box. */
  clearManual(): void {
    this.manualQuestion = '';
    this.audioStatus = '';
  }

  /** Copy the full answer (points + example + follow-ups) to the clipboard. */
  async copyAnswer(): Promise<void> {
    const text = this.answerText;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      this.copied = true;
      setTimeout(() => (this.copied = false), 2000);
    } catch {
      this.copied = false;
    }
  }

  /** Copy the interviewer transcript (meeting audio) to the clipboard. */
  async copyTranscript(): Promise<void> {
    const text = this.lastInterviewerText.trim();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      this.copiedTranscript = true;
      setTimeout(() => (this.copiedTranscript = false), 2000);
    } catch {
      this.copiedTranscript = false;
    }
  }

  /** Changing depth persists it and refreshes the current answer immediately. */
  onLengthChange(level: ResponseLength): void {
    const previous = this.responseLength;
    this.responseLength = level;
    this.settings.save({ responseLength: level });
    if (previous !== level && this.guidance) {
      // Re-ask the current question at the new depth so the user sees a diff.
      this.onQuestion(this.guidance.question);
    }
  }

  // ------------------------------------------------------- guidance text size

  /** Apply and persist a new AI Guidance text size (px). */
  onFontSizeChange(size: number): void {
    const next = Math.min(this.maxFontSize, Math.max(this.minFontSize, Math.round(Number(size) || 15)));
    this.guidanceFontSize = next;
    this.settings.save({ guidanceFontSize: next });
  }

  /** Step the text size down by 1px (clamped). */
  decreaseFont(): void {
    this.onFontSizeChange(this.guidanceFontSize - 1);
  }

  /** Step the text size up by 1px (clamped). */
  increaseFont(): void {
    this.onFontSizeChange(this.guidanceFontSize + 1);
  }

  /** Back to the default reading size. */
  resetFont(): void {
    this.onFontSizeChange(15);
  }

  isStarObject(): boolean {
    return !!this.guidance?.star && typeof this.guidance.star === 'object';
  }

  starEntries(): Array<[string, string]> {
    const s = this.guidance?.star;
    if (s && typeof s === 'object') {
      return Object.entries(s).map(([k, v]) => [k, String(v)]);
    }
    return [];
  }

  private onTranscript(text: string, isFinal: boolean): void {
    if (!isFinal) return;
    this.entries.push({ speaker: 'interviewer', text, timestamp: new Date(), isQuestion: QuestionDetectorService.detect(text) });
    this.detector.pushFragment(text);
    if (this.ws.isConnected()) this.ws.sendTranscript(text, this.responseLength);
  }

  private onQuestion(q: string): void {
    this.ws.setStatus('thinking');
    this.thinking = true;
    if (this.ws.isConnected()) {
      // Transcript fragments were already streamed by onTranscript, so ask the
      // server to analyze its buffer instead of re-sending the same text
      // (re-sending duplicated the question and short questions never fired).
      this.ws.sendForce(q, this.responseLength);
      return;
    }
    // HTTP fallback when WS unavailable
    this.resumeService.analyzeQuestion(q, this.responseLength).subscribe({
      next: (g) => {
        this.thinking = false;
        this.ws.pushLocalGuidance({ ...g, question: q });
      },
      error: () => {
        this.thinking = false;
        this.ws.pushLocalGuidance(this.demo.guidanceFor(q));
        this.error = 'Backend unreachable — showing on-device demo guidance. Start the backend for live AI answers.';
      },
    });
  }

  private startTimer(): void {
    this.stopTimer();
    this.timer = setInterval(() => this.elapsed++, 1000);
  }

  private stopTimer(keep = false): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    if (!keep) { /* keep elapsed for session record */ }
  }
}
