import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { MockFeedback, MockQuestionsResponse } from '../../core/models/mock-interview.models';
import { WebSpeechProvider } from '../../core/services/speech/web-speech.provider';
import { routes } from '../../app.routes';
import { authGuard } from '../../core/auth/auth.guard';
import { MockInterviewComponent } from './mock-interview.component';

const QUESTIONS: MockQuestionsResponse = {
  source: 'ai',
  questions: [
    { id: 1, question: 'Tell me about yourself.', type: 'hr', focus: 'A short story.' },
    { id: 2, question: 'Describe a conflict.', type: 'behavioral', focus: 'STAR.' },
  ],
};

const fb = (score: number): MockFeedback => ({
  score,
  summary: 'Solid start.',
  strengths: ['Clear'],
  improvements: ['Add a metric'],
  sampleAnswer: ['Role', 'Impact'],
  source: 'ai',
});

class FakeSpeech {
  cb: ((t: { text: string; isFinal: boolean }) => void) | null = null;
  started = 0;
  stopped = 0;
  isSupported() { return true; }
  onTranscript(cb: (t: { text: string; isFinal: boolean }) => void) { this.cb = cb; }
  start() { this.started++; }
  stop() { this.stopped++; }
}

describe('MockInterviewComponent', () => {
  let http: HttpTestingController;
  let speech: FakeSpeech;

  beforeEach(() => {
    localStorage.setItem('aia_position', 'Java Developer');
    TestBed.configureTestingModule({
      imports: [MockInterviewComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    speech = new FakeSpeech();
    TestBed.overrideComponent(MockInterviewComponent, {
      set: { providers: [{ provide: WebSpeechProvider, useValue: speech }] },
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    http.verify();
    localStorage.removeItem('aia_position');
  });

  function create() {
    const f = TestBed.createComponent(MockInterviewComponent);
    f.detectChanges();
    return f;
  }

  function startWith(f: ReturnType<typeof create>, res = QUESTIONS) {
    f.componentInstance.start();
    const req = http.expectOne((r) => r.url.endsWith('/api/mock-interview/questions'));
    req.flush(res);
    f.detectChanges();
    return req;
  }

  it('prefills the role and sends the chosen setup', () => {
    const f = create();
    expect(f.componentInstance.role).toBe('Java Developer');
    f.componentInstance.interviewType = 'behavioral';
    f.componentInstance.difficulty = 'hard';
    f.componentInstance.count = 3;
    const req = startWith(f);
    expect(req.request.body).toEqual(jasmine.objectContaining({
      role: 'Java Developer', interviewType: 'behavioral', difficulty: 'hard', count: 3,
    }));
    const el = f.nativeElement as HTMLElement;
    expect(el.textContent).toContain('Question 1 of 2');
    expect(el.textContent).toContain('Tell me about yourself.');
  });

  it('scores an answer, keeps earlier attempts on retry and summarises best scores', () => {
    const f = create();
    const c = f.componentInstance;
    startWith(f);

    c.answer = 'I am a backend developer with five years of Java.';
    c.submit();
    const r1 = http.expectOne((r) => r.url.endsWith('/api/mock-interview/feedback'));
    expect(r1.request.body).toEqual(jasmine.objectContaining({
      question: 'Tell me about yourself.', questionType: 'hr',
    }));
    r1.flush(fb(5));
    f.detectChanges();
    expect(c.phase()).toBe('reviewing');
    expect((f.nativeElement as HTMLElement).textContent).toContain('Add a metric');

    c.retry();
    expect(c.answer).toBe('');
    c.answer = 'Better answer with impact: cut latency by 40%.';
    c.submit();
    http.expectOne((r) => r.url.endsWith('/api/mock-interview/feedback')).flush(fb(8));

    c.next();
    expect(c.index()).toBe(1);
    c.skip(); // last question → summary
    f.detectChanges();
    expect(c.phase()).toBe('summary');
    expect(c.bestScores()).toEqual([8, null]);
    expect(c.average()).toBe(8);
    expect((f.nativeElement as HTMLElement).textContent).toContain('2 attempts');
  });

  it('does not submit an empty answer', () => {
    const f = create();
    startWith(f);
    f.componentInstance.answer = '   ';
    f.componentInstance.submit();
    http.expectNone((r) => r.url.endsWith('/api/mock-interview/feedback'));
  });

  it('appends final speech transcripts to the answer and stops on submit', () => {
    const f = create();
    const c = f.componentInstance;
    startWith(f);
    c.toggleListening();
    expect(speech.started).toBe(1);
    speech.cb!({ text: 'partial', isFinal: false });
    speech.cb!({ text: 'I led the migration.', isFinal: true });
    speech.cb!({ text: 'It saved 2 hours a day.', isFinal: true });
    expect(c.answer).toBe('I led the migration. It saved 2 hours a day.');
    c.submit();
    expect(speech.stopped).toBe(1);
    http.expectOne((r) => r.url.endsWith('/api/mock-interview/feedback')).flush(fb(7));
  });

  it('shows an error and stays on setup when questions fail', () => {
    const f = create();
    f.componentInstance.start();
    http.expectOne((r) => r.url.endsWith('/api/mock-interview/questions'))
      .flush('boom', { status: 502, statusText: 'Bad Gateway' });
    f.detectChanges();
    expect(f.componentInstance.phase()).toBe('setup');
    expect(f.componentInstance.error()).toContain('Could not start');
  });

  it('is a signed-in, non-indexed route', () => {
    const route = routes.find((r) => r.path === 'mock-interview')!;
    expect(route.canActivate).toContain(authGuard);
    expect((route.data?.['seo'] as { noindex?: boolean }).noindex).toBeTrue();
  });
});
