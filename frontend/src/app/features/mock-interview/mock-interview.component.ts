import { Component, OnDestroy, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import {
  MockDifficulty,
  MockFeedback,
  MockInterviewType,
  MockQuestion,
  MockSource,
} from '../../core/models/mock-interview.models';
import { MockInterviewService } from '../../core/services/mock-interview.service';
import { ResumeService } from '../../core/services/resume.service';
import { WebSpeechProvider } from '../../core/services/speech/web-speech.provider';
import { TargetRoleService } from '../../core/services/target-role.service';
import { ErrorBannerComponent } from '../../shared/components/error-banner/error-banner.component';

type Phase = 'setup' | 'answering' | 'reviewing' | 'summary';

/** Per-question progress: every attempt's score, so retries show improvement. */
interface QuestionResult {
  scores: number[];
  last: MockFeedback | null;
}

/**
 * Mock interview: pick a role and type, answer role-based practice questions
 * in your own words (typed or spoken), and get a score with feedback. Retry a
 * question as often as you like; the summary shows your best score for each.
 *
 * Uses its own speech-recognition instance, so it never touches the shared
 * transcription used elsewhere in the app.
 */
@Component({
  selector: 'app-mock-interview',
  standalone: true,
  imports: [FormsModule, RouterLink, ErrorBannerComponent],
  providers: [WebSpeechProvider],
  templateUrl: './mock-interview.component.html',
  styleUrl: './mock-interview.component.css',
})
export class MockInterviewComponent implements OnDestroy {
  private readonly api = inject(MockInterviewService);
  private readonly resume = inject(ResumeService);
  private readonly roles = inject(TargetRoleService);
  private readonly speech = inject(WebSpeechProvider);

  readonly types: { key: MockInterviewType; label: string }[] = [
    { key: 'mixed', label: 'Mixed' },
    { key: 'technical', label: 'Technical' },
    { key: 'behavioral', label: 'Behavioral (STAR)' },
    { key: 'hr', label: 'HR' },
  ];
  readonly difficulties: MockDifficulty[] = ['easy', 'medium', 'hard'];
  readonly counts = [3, 5, 8];

  role = this.roles.localPosition;
  interviewType: MockInterviewType = 'mixed';
  difficulty: MockDifficulty = 'medium';
  count = 5;
  answer = '';

  readonly phase = signal<Phase>('setup');
  readonly busy = signal(false);
  readonly error = signal('');
  readonly questions = signal<MockQuestion[]>([]);
  readonly questionSource = signal<MockSource>('ai');
  readonly index = signal(0);
  readonly results = signal<QuestionResult[]>([]);
  readonly listening = signal(false);
  readonly speechSupported = this.speech.isSupported();

  readonly current = computed(() => this.questions()[this.index()] ?? null);
  readonly feedback = computed(() => this.results()[this.index()]?.last ?? null);
  readonly isLast = computed(() => this.index() >= this.questions().length - 1);
  /** Best score per answered question (unanswered ones are left out). */
  readonly bestScores = computed(() =>
    this.results().map((r) => (r.scores.length ? Math.max(...r.scores) : null)),
  );
  readonly average = computed(() => {
    const done = this.bestScores().filter((s): s is number => s !== null);
    return done.length ? Math.round((done.reduce((a, b) => a + b, 0) / done.length) * 10) / 10 : null;
  });

  constructor() {
    this.speech.onTranscript((t) => {
      if (!t.isFinal || !t.text) return;
      this.answer = this.answer ? `${this.answer.trimEnd()} ${t.text}` : t.text;
    });
  }

  ngOnDestroy(): void {
    this.stopListening();
  }

  start(): void {
    if (this.busy()) return;
    this.error.set('');
    this.busy.set(true);
    this.api
      .questions({
        role: this.role.trim(),
        jobDescription: this.resume.jobDescription,
        resumeText: this.resume.resumeText,
        interviewType: this.interviewType,
        difficulty: this.difficulty,
        count: this.count,
      })
      .subscribe({
        next: (res) => {
          this.busy.set(false);
          if (!res.questions.length) {
            this.error.set('No questions came back. Please try again.');
            return;
          }
          this.questions.set(res.questions);
          this.questionSource.set(res.source);
          this.results.set(res.questions.map(() => ({ scores: [], last: null })));
          this.index.set(0);
          this.answer = '';
          this.phase.set('answering');
        },
        error: () => {
          this.busy.set(false);
          this.error.set('Could not start the mock interview. Please try again.');
        },
      });
  }

  submit(): void {
    const q = this.current();
    const answer = this.answer.trim();
    if (!q || !answer || this.busy()) return;
    this.stopListening();
    this.error.set('');
    this.busy.set(true);
    this.api
      .feedback({
        question: q.question,
        questionType: q.type,
        answer,
        role: this.role.trim(),
        jobDescription: this.resume.jobDescription,
        resumeText: this.resume.resumeText,
      })
      .subscribe({
        next: (fb) => {
          this.busy.set(false);
          const i = this.index();
          this.results.update((rs) =>
            rs.map((r, j) => (j === i ? { scores: [...r.scores, fb.score], last: fb } : r)),
          );
          this.phase.set('reviewing');
        },
        error: () => {
          this.busy.set(false);
          this.error.set('Feedback is unavailable right now. Your answer is still here — try again.');
        },
      });
  }

  /** Answer the same question again (earlier scores are kept). */
  retry(): void {
    this.answer = '';
    this.results.update((rs) => rs.map((r, j) => (j === this.index() ? { ...r, last: null } : r)));
    this.phase.set('answering');
  }

  next(): void {
    if (this.isLast()) {
      this.phase.set('summary');
      return;
    }
    this.index.update((i) => i + 1);
    this.answer = '';
    this.phase.set('answering');
  }

  skip(): void {
    this.stopListening();
    this.next();
  }

  restart(): void {
    this.stopListening();
    this.questions.set([]);
    this.results.set([]);
    this.index.set(0);
    this.answer = '';
    this.error.set('');
    this.phase.set('setup');
  }

  toggleListening(): void {
    if (this.listening()) {
      this.stopListening();
      return;
    }
    this.speech.start();
    this.listening.set(true);
  }

  private stopListening(): void {
    if (!this.listening()) return;
    this.speech.stop();
    this.listening.set(false);
  }

  scoreClass(score: number | null): string {
    if (score === null) return '';
    return score >= 8 ? 'good' : score >= 5 ? 'ok' : 'low';
  }

  typeLabel(type: string): string {
    return type === 'hr' ? 'HR' : type.charAt(0).toUpperCase() + type.slice(1);
  }
}
