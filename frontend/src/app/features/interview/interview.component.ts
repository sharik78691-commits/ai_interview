import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { Subscription } from 'rxjs';
import { environment } from '../../../environments/environment';
import {
  AIInterviewResponse,
  ComparisonTable,
  FollowUpItem,
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
import { AiModelsService } from '../../core/services/ai-models.service';
import { AiModel } from '../../core/models/ai-models';
import { TranscriptionService } from '../../core/services/transcription.service';
import { ErrorBannerComponent } from '../../shared/components/error-banner/error-banner.component';
import { TimeFormatPipe } from '../../shared/pipes/time-format.pipe';
import { RichTextPipe } from '../../shared/pipes/rich-text.pipe';
import { SmoothText } from './smooth-text';

/** View model for the adaptive Interview Answer Card. */
export interface AnswerCard {
  typeLabel: string;
  isCoding: boolean;
  /** Best Answer sentences grouped into short, speakable paragraphs. */
  bestAnswer: string[];
  /**
   * List-style Best Answer (e.g. the four OOP concepts): intro paragraphs,
   * one numbered item per "**Term:** ..." point, then closing paragraphs.
   * `bestItems` is empty for ordinary answers, which use `bestAnswer`.
   */
  bestIntro: string[];
  bestItems: string[];
  bestOutro: string[];
  keyDetails: string[];
  /** Quick-reference table (multi-part / compare questions), or null. */
  table: ComparisonTable | null;
  followUps: FollowUpItem[];
}

const EMPTY_CARD: AnswerCard = {
  typeLabel: '',
  isCoding: false,
  bestAnswer: [],
  bestIntro: [],
  bestItems: [],
  bestOutro: [],
  keyDetails: [],
  table: null,
  followUps: [],
};

/** A point that opens with a bold label: "**Term:** ..." or "**Term**: ...". */
const LABELLED_POINT = /^\s*\*\*[^*\n]{1,80}?(?::\*\*|\*\*\s*[:—-])/;

/**
 * Split a Best Answer into intro / numbered items / outro when it is a
 * list-style answer (2+ labelled points); otherwise everything stays in intro.
 */
export function splitLabelled(points: string[]): { intro: string[]; items: string[]; outro: string[] } {
  const labelled = points.filter((p) => LABELLED_POINT.test(p));
  if (labelled.length < 2) return { intro: points, items: [], outro: [] };
  const first = points.findIndex((p) => LABELLED_POINT.test(p));
  return {
    intro: points.slice(0, first),
    items: labelled,
    outro: points.slice(first).filter((p) => !LABELLED_POINT.test(p)),
  };
}

/** Usable table: has headers and at least one row. */
function tableOf(t: ComparisonTable | null | undefined): ComparisonTable | null {
  return t && t.headers?.length && t.rows?.length ? t : null;
}

/**
 * Group spoken sentences into paragraphs of at most two, so the Best Answer
 * reads like natural speech without becoming a wall of text.
 */
export function groupSentences(sentences: string[]): string[] {
  const out: string[] = [];
  for (let i = 0; i < sentences.length; i += 2) {
    out.push(sentences.slice(i, i + 2).join(' '));
  }
  return out;
}

/** "Earlier this session" in AI Guidance shows at most this many past answers. */
export const MAX_PAST_HISTORY = 4;
/** How long a finished streamed utterance waits for its final transcript. */
const STREAM_HOLD_MS = 2_500;

@Component({
  selector: 'app-interview',
  standalone: true,
  imports: [FormsModule, ErrorBannerComponent, TimeFormatPipe, RichTextPipe],
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
  private aiModels = inject(AiModelsService);
  private route = inject(ActivatedRoute);
  /** True in the packaged desktop build (Auto Select + transparency slider). */
  readonly isDesktop = environment.electron;

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
  responseLength: ResponseLength = 'default';
  readonly lengthOptions = Object.entries(RESPONSE_LENGTH_LABELS) as Array<[
    ResponseLength,
    string,
  ]>;

  // ------------------------------------------------------------- AI model selection
  /** Model catalog from the backend (names, providers, availability). */
  aiModelList: AiModel[] = [];
  /** Currently selected model key (persisted; default from backend). */
  selectedModel = '';
  /** Model/provider that produced the latest guidance (display only). */
  lastUsedModel = '';
  lastUsedProvider = '';
  /** Provisional streamed bullets (preview only — final card replaces them). */
  provisionalPoints: string[] = [];
  /** True while a streamed answer is in flight. */
  isStreaming = false;
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
  /** Copy the interviewer transcript to the clipboard. */
  copiedTranscript = false;
  /** Last interviewer question recognised from meeting/tab audio. */
  lastInterviewerText = '';
  /**
   * PROVISIONAL meeting-audio text while the interviewer is still talking
   * (live transcript). Display only: it never triggers an answer, and the
   * final transcript of the same clip replaces it.
   */
  interviewerInterim = '';
  /** Clip seq the interim text belongs to (-1 = none). */
  private interimSeq = -1;
  /** Word-by-word streamed text of the current utterance (display only). */
  private streamText = '';
  /** True while the streamed utterance is still being spoken. */
  private streamOpen = false;
  private streamHold: ReturnType<typeof setTimeout> | null = null;
  /** Typewriter-smoothed live line actually rendered. */
  private readonly liveTyped = new SmoothText();
  /** Scope of the current notice: AI fallback vs. speech-to-text problem. */
  noticeScope: 'ai' | 'stt' = 'ai';
  /** One transcription request is in flight; extra clips wait their turn. */
  audioBusy = false;
  private clipQueue: { data: ArrayBuffer; mimeType: string }[] = [];
  /**
   * Audio-input labels the browser can see, set when "Auto Select" needs the
   * user to enable a system-audio device. `null` means no setup is required.
   */
  systemAudioDevices: string[] | null = null;
  /** Info popup shown before "Auto Select Meeting Audio" starts capturing. */
  autoSelectInfoOpen = false;

  /** On-demand "If they ask further": loading flag + last error for the current card. */
  followUpsLoading = false;
  followUpsError = '';

  /** "Show follow-ups": ask the backend for this answer's likely next questions. */
  showFollowUps(): void {
    const q = this.guidance?.question?.trim();
    if (!q || this.followUpsLoading) return;
    this.followUpsError = '';
    if (!this.ws.sendRequestFollowUps(q)) {
      this.followUpsError = 'Not connected. Try again in a moment.';
      return;
    }
    this.followUpsLoading = true;
  }

  /** Merge an ai_followups reply into the current card (late replies ignored). */
  applyFollowUps({ question, followUps, error }: { question: string; followUps: FollowUpItem[]; error?: string }): void {
    const g = this.guidance;
    // Ignore a late reply for an answer that is no longer on screen.
    if (!g || norm(question) !== norm(g.question)) return;
    this.followUpsLoading = false;
    if (followUps.length) {
      // New object so the memoized card is rebuilt with the follow-ups.
      this.guidance = { ...g, followUps };
      this.followUpsError = '';
    } else {
      this.followUpsError = error || 'No follow-ups were suggested. Try again.';
    }
  }

  /** Display labels for the backend's question types. */
  private static readonly TYPE_LABELS: Record<string, string> = {
    technical: 'Theory',
    behavioral: 'Scenario',
    coding: 'Coding',
    general: 'HR',
  };

  private cardFor: AIInterviewResponse | null = null;
  private cardCache: AnswerCard = EMPTY_CARD;

  /**
   * View model for the adaptive Interview Answer Card, memoized per answer so
   * change detection does not rebuild arrays. Falls back to the legacy
   * `keyTakeaways` / `followUpQuestions` fields for older payloads.
   */
  get card(): AnswerCard {
    const g = this.guidance;
    if (!g) return EMPTY_CARD;
    if (g === this.cardFor) return this.cardCache;
    const followUps: FollowUpItem[] = g.followUps?.length
      ? g.followUps.filter((f) => f?.question)
      : (g.followUpQuestions ?? []).map((f) => {
          const [question, ...rest] = f.split(' - ');
          return { question: question.trim(), answer: rest.join(' - ').trim() };
        });
    const isCoding = g.questionType === 'coding' && !!(g.codeHint || g.approach);
    this.cardFor = g;
    const split = splitLabelled(g.answerPoints);
    this.cardCache = {
      typeLabel: InterviewComponent.TYPE_LABELS[g.questionType] ?? g.questionType,
      isCoding,
      bestAnswer: groupSentences(g.answerPoints),
      bestIntro: groupSentences(split.intro),
      bestItems: split.items,
      bestOutro: groupSentences(split.outro),
      keyDetails: g.keyDetails?.length ? g.keyDetails : (g.keyTakeaways ?? []),
      table: isCoding ? null : tableOf(g.comparison),
      followUps,
    };
    return this.cardCache;
  }

  /** Provisional (streamed) Best Answer, grouped the same way as the final card. */
  get draftParagraphs(): string[] {
    return groupSentences(this.provisionalPoints);
  }
  elapsed = 0;
  live = false;
  /**
   * True while an interview session is in progress — demo running or meeting
   * audio being captured. Single source of truth for the topbar status pill
   * and session clock (previously the bar could show "Idle 00:00" mid-interview).
   */
  get sessionActive(): boolean {
    return this.live || this.capturingInterviewer;
  }
  private timer: ReturnType<typeof setInterval> | null = null;
  private subs: Subscription[] = [];

  ngOnInit(): void {
    if (this.route.snapshot.queryParamMap.get('auto') === '1') {
      this.autoSelectInfoOpen = true;
    }
    this.responseLength = this.settings.value.responseLength;
    this.guidanceFontSize = this.settings.value.guidanceFontSize;
    // Load the AI model catalog and adopt the persisted (or default) model.
    this.aiModels.listModels().subscribe((models) => {
      this.aiModelList = models;
      this.selectedModel = this.resolveModel(models);
    });
    this.ws.connect();
    this.transcription.connect();
    this.micSupported = this.transcription.isSupported();
    // Never START the Web Speech recognizer here: it can only hear the
    // CANDIDATE'S microphone, so the user's own spoken answers were being
    // transcribed, detected as questions and answered by the AI. Questions
    // come only from interviewer (meeting/system) audio via backend STT, plus
    // typed/demo text pushed through transcript$.
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
          // A new answer: forget the previous card's follow-up request state.
          if (g.question !== this.guidance?.question) {
            this.followUpsLoading = false;
            this.followUpsError = '';
          }
          this.guidance = g;
          this.thinking = false;
          this.flash = false;
          requestAnimationFrame(() => (this.flash = true));
        }
      }),
      this.ws.followUps$.subscribe((r) => this.applyFollowUps(r)),
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
          this.clearInterviewerInterim();
          this.dropStreamText();
        }
      }),
      // One clip in flight at a time. Sending every clip immediately used to
      // hit the transcription rate limit during a fast interview.
      this.interviewerAudio.clip$.subscribe((clip) => this.enqueueClip(clip)),
      // Live transcript: snapshots bypass the clip queue (the backend keeps
      // only the newest one) and are sent only when the backend supports them.
      // Word-by-word stream when the backend has it; snapshots otherwise.
      this.ws.livePreview$.subscribe(() => this.syncLiveFlags()),
      this.ws.liveStream$.subscribe(() => this.syncLiveFlags()),
      this.interviewerAudio.preview$.subscribe((p) => this.ws.sendAudioPreview(p.data, p.mimeType)),
      this.interviewerAudio.livePcm$.subscribe((frame) => this.ws.sendLivePcm(frame)),
      this.ws.liveText$.subscribe((t) => this.onLiveText(t)),
      // When Auto Select needs a system-audio device, surface the setup helper
      // (with the list of devices the browser currently sees).
      this.interviewerAudio.systemAudioSetup$.subscribe(
        (devices) => (this.systemAudioDevices = devices),
      ),
      // Server-side STT result: show it, and let the backend's own question
      // detection decide when to call the AI (no extra AI call here).
      this.ws.interviewerTranscript$.subscribe((t) => this.onInterviewerTranscript(t)),
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
          } else if (/Already answered/i.test(msg)) {
            // Duplicate suppressed server-side: no new answer is coming, so
            // clear the "composing" spinner and stay on the existing answer.
            this.audioStatus = 'Answer ready';
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
      // Track which model/provider produced the latest guidance.
      this.ws.lastModel$.subscribe((m) => (this.lastUsedModel = m ?? '')),
      this.ws.lastProvider$.subscribe((p) => (this.lastUsedProvider = p ?? '')),
      // Provisional streamed bullets (preview only, never the final answer).
      this.ws.streamPoints$.subscribe((pts) => (this.provisionalPoints = pts)),
      this.ws.streaming$.subscribe((s) => (this.isStreaming = s)),
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
    this.ws.sendAudioClip(clip.data, clip.mimeType, this.responseLength, this.activeModel());
    // Safety valve: never stall the queue if the backend stays silent.
    setTimeout(() => this.releaseClipSlot(), 10000);
  }

  private releaseClipSlot(): void {
    this.audioBusy = false;
    this.pumpClipQueue();
  }

  /**
   * Server-side STT result for meeting audio. A provisional (final:false)
   * transcript only updates the live line; it neither frees the clip slot nor
   * reaches question detection, so it can never start an answer.
   */
  onInterviewerTranscript(t: { text: string; final: boolean; seq?: number }): void {
    if (!t.final) {
      if (!t.text) return;
      // A preview of an older clip than the one shown is out of date.
      if (t.seq !== undefined && t.seq < this.interimSeq) return;
      this.interviewerInterim = t.text;
      this.interimSeq = t.seq ?? this.interimSeq;
      this.syncLive();
      return;
    }
    // Meeting audio has its own dedicated card ("Interviewer (meeting audio)")
    // above the transcript list, so it is NOT duplicated into entries —
    // the list stays for microphone / demo speech only.
    this.lastInterviewerText = t.text;
    // The final replaces its own clip's live text; a newer clip's stays.
    if (t.seq === undefined || t.seq >= this.interimSeq) this.clearInterviewerInterim();
    // A finished streamed utterance yields to the final transcript; one still
    // being spoken (the interviewer only paused) keeps growing on screen.
    if (!this.streamOpen) this.dropStreamText();
    this.releaseClipSlot();
  }

  /**
   * Word-by-word streamed text (cumulative per utterance). Display only: it
   * never reaches question detection, so it can never start an answer.
   */
  onLiveText(t: { text: string; final: boolean }): void {
    if (this.streamHold) clearTimeout(this.streamHold);
    this.streamHold = null;
    if (!t.text) return;
    this.streamText = t.text;
    this.streamOpen = !t.final;
    // A closed turn stays up until the clip's final transcript replaces it
    // (normally well under a second); the timer only covers a final that
    // never comes, so the line cannot get stuck on old words.
    if (t.final) this.streamHold = setTimeout(() => this.dropStreamText(), STREAM_HOLD_MS);
    this.syncLive();
  }

  private dropStreamText(): void {
    if (this.streamHold) clearTimeout(this.streamHold);
    this.streamHold = null;
    this.streamText = '';
    this.streamOpen = false;
    this.syncLive();
  }

  private clearInterviewerInterim(): void {
    this.interviewerInterim = '';
    this.interimSeq = -1;
    this.syncLive();
  }

  private syncLive(): void {
    this.liveTyped.set(this.streamText || this.interviewerInterim);
  }

  private syncLiveFlags(): void {
    const stream = this.ws.liveStream$.value;
    this.interviewerAudio.liveStream = stream;
    // The stream supersedes snapshots: never pay for both.
    this.interviewerAudio.livePreview = this.ws.livePreview$.value && !stream;
  }

  /** Live (provisional) text for the Transcript line: mic first, then meeting audio. */
  get liveTranscript(): string {
    return this.interim || this.liveTyped.shown;
  }

  /**
   * Past Q&As excluding the one shown in the current-guidance card — newest
   * first, at most MAX_PAST_HISTORY ("Earlier this session").
   */
  get pastHistory(): HistoryItem[] {
    if (this.history.length <= 1) return [];
    return this.history.slice(0, -1).reverse().slice(0, MAX_PAST_HISTORY);
  }

  ngOnDestroy(): void {
    this.subs.forEach((s) => s.unsubscribe());
    this.liveTyped.stop();
    if (this.streamHold) clearTimeout(this.streamHold);
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
      default: return this.sessionActive ? 'Live' : 'Idle';
    }
  }

  /** Rough word budget shown next to the depth selector. */
  get lengthHint(): string {
    switch (this.responseLength) {
      case 'short': return '3–4 quick bullets';
      default: return 'Complete answer + full interviewer checklist';
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

  /**
   * Web build: capture the interviewer through the browser's "share a tab /
   * meeting" picker. The desktop build uses `startSystemAudio()` (Auto Select)
   * instead, so the website path stays exactly as it was.
   */
  async startInterviewerAudio(): Promise<void> {
    this.error = '';
    try {
      await this.interviewerAudio.start();
      this.ws.sendInterviewerAudioStart('audio/wav');
      // A live session starts here too: the clock runs and the pill leaves Idle.
      this.startTimer();
    } catch {
      // Status + user-facing error already set by the service.
      this.error = this.interviewerAudio.status$.value;
    }
  }

  /** Exact reason tab/meeting audio cannot be used (null when supported). */
  get captureBlockReason(): string | null {
    return this.interviewerAudio.getBlockReason();
  }

  /** Open the info popup for "Auto Select Meeting Audio". */
  openAutoSelectInfo(): void {
    this.autoSelectInfoOpen = true;
  }

  /** Close the info popup without capturing. */
  closeAutoSelectInfo(): void {
    this.autoSelectInfoOpen = false;
  }

  /** User confirmed the popup: close it and start system-audio capture. */
  confirmAutoSelect(): void {
    this.autoSelectInfoOpen = false;
    // Desktop keeps its loopback-only path; the web build adds the screen-audio fallback.
    void (this.isDesktop ? this.startSystemAudio() : this.autoSelectMeetingAudio());
  }

  /**
   * "Auto Select": start capturing the computer's own sound (system output)
   * through a loopback device, with no browser "share a tab" picker. The AI
   * then hears the interviewer automatically and answers in real time.
   */
  async startSystemAudio(): Promise<void> {
    this.error = '';
    try {
      // Desktop: Windows loopback straight from the Electron main process — no
      // picker, no Stereo Mix. Falls back to a loopback input device below.
      const started =
        (this.isDesktop && (await this.interviewerAudio.startDesktopLoopbackAudio())) ||
        (await this.interviewerAudio.startSystemAudio());
      // `started === false` means no loopback device exists yet; the service
      // has already flagged setup and the inline helper takes over.
      if (started) {
        this.ws.sendInterviewerAudioStart('audio/wav');
        // A live session starts here too: the clock runs and the pill leaves Idle.
        this.startTimer();
      }
    } catch {
      // The service already put a specific reason in status$.
      this.error = this.interviewerAudio.status$.value;
    }
  }

  /**
   * Web "Auto Select Meeting Audio": one click captures the COMPUTER'S SOUND
   * through a system-audio loopback input (Stereo Mix / VB-Cable / BlackHole)
   * — never the share picker. Picking a tab/screen is what "Select meeting
   * audio" is for; Auto Select must not duplicate it. When no loopback device
   * is enabled, the service flags setup and the inline helper explains how to
   * turn one on, instead of silently falling back to the picker.
   */
  async autoSelectMeetingAudio(): Promise<void> {
    if (this.capturingInterviewer) return;
    await this.startSystemAudio();
  }

  /**
   * Download a tiny .cmd file that, when double-clicked, runs
   * `control mmsys.cpl,,1` — opening the Sound panel straight on the Recording
   * tab (where "Stereo Mix" is enabled). Generated on the client so there is no
   * static asset to host and no copy-paste step for the user.
   */
  downloadSoundPanelCmd(): void {
    const content = '@echo off\r\ncontrol mmsys.cpl,,1\r\n';
    const blob = new Blob([content], { type: 'application/octet-stream' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'open-sound-panel.cmd';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  /** Stop capturing interviewer audio. */
  async stopInterviewerAudio(): Promise<void> {
    await this.interviewerAudio.stop();
    this.ws.sendInterviewerAudioStop();
    // Meeting session ended: the session clock goes back to 00:00.
    // (Left running when a demo is playing so the demo clock is not disturbed.)
    if (!this.live) {
      this.stopTimer();
      this.elapsed = 0;
    }
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
    this.lastQuestion = question;
    if (this.ws.isConnected()) {
      this.ws.sendInterviewerQuestion(question, this.responseLength, this.activeModel());
    } else {
      this.resumeService.analyzeQuestion(question, this.responseLength, this.activeModel()).subscribe({
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

  /** Last question asked (enables the Retry button after failures). */
  lastQuestion = '';

  /** Changing depth persists it and refreshes the current answer immediately. */
  onLengthChange(level: ResponseLength): void {    const previous = this.responseLength;
    this.responseLength = level;
    this.settings.save({ responseLength: level });
    if (previous !== level && this.guidance) {
      // Re-ask the current question at the new depth so the user sees a diff.
      this.onQuestion(this.guidance.question);
    }
  }

  // ---------------------------------------------------------- AI model select

  /** Pick the persisted model when it is still offered, else the default. */
  private resolveModel(models: AiModel[]): string {
    const saved = (this.settings.value.aiModel || '').trim();
    if (saved && saved !== 'auto' && models.some((m) => m.key === saved)) {
      return saved;
    }
    return models.find((m) => m.isDefault)?.key ?? '';
  }

  /** Resolve the model key to send: '' means "backend default". */
  private activeModel(): string | undefined {
    const key = (this.selectedModel || '').trim();
    return key ? key : undefined;
  }

  /** Detail row for the currently selected model (capabilities + status). */
  selectedModelDetail(): AiModel | null {
    return this.aiModelList.find((m) => m.key === this.selectedModel) ?? null;
  }

  /** Label for the currently selected model (selector + status line). */
  selectedModelLabel(): string {
    return (
      this.aiModelList.find((m) => m.key === this.selectedModel)?.displayName ||
      this.selectedModel ||
      'Default'
    );
  }

  /** Switch the AI model for subsequent answers (mid-session, persisted). */
  onModelChange(key: string): void {
    this.selectedModel = key;
    this.settings.save({ aiModel: key });
    this.ws.sendSetModel(key);
  }

  /** Re-ask the last question (e.g. after a rate-limit error) with the current model. */
  retryGuidance(): void {
    const q = (this.lastQuestion || this.guidance?.question || '').trim();
    if (!q) return;
    this.error = '';
    this.onQuestion(q);
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
    // The Transcript panel shows only the latest interviewer message.
    this.lastInterviewerText = text;
    this.detector.pushFragment(text);
    // Demo mode is fully on-device: never forward demo speech to the server,
    // or every demo question gets TWO answers (demo + live AI).
    if (this.ws.isConnected() && !this.live) this.ws.sendTranscript(text, this.responseLength, this.activeModel());
  }

  private onQuestion(q: string): void {
    // Same demo guard as onTranscript: the demo sequence drives guidance via
    // onGuidance, so a detector emission must not also fire server AI.
    if (this.live) return;
    this.lastQuestion = q;
    this.ws.setStatus('thinking');
    this.thinking = true;
    if (this.ws.isConnected()) {
      // Transcript fragments were already streamed by onTranscript, so ask the
      // server to analyze its buffer instead of re-sending the same text
      // (re-sending duplicated the question and short questions never fired).
      this.ws.sendForce(q, this.responseLength, this.activeModel());
      return;
    }
    // HTTP fallback when WS unavailable
    this.resumeService.analyzeQuestion(q, this.responseLength, this.activeModel()).subscribe({
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

/** Whitespace/case-insensitive question match (server echoes the question). */
function norm(q: string | undefined | null): string {
  return (q ?? '').trim().replace(/\s+/g, ' ').toLowerCase();
}
