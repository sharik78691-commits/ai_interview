// Global `window.electronAPI` typing the component references (types only).
import '../../core/services/electron-bridge';
import { AIInterviewResponse } from '../../core/models/interview.models';
import { DemoService } from '../../core/services/demo.service';
import { InterviewComponent, MAX_PAST_HISTORY, MAX_SESSION_SECONDS, groupSentences, splitLabelled } from './interview.component';
import { SmoothText } from './smooth-text';
import { EMPTY, Subject } from 'rxjs';

describe('DemoService', () => {
  it('returns saga guidance for saga questions', () => {
    const d = new DemoService();
    expect(d.guidanceFor('Explain the saga pattern?').questionType).toBe('technical');
  });

  it('falls back to general guidance', () => {
    const d = new DemoService();
    expect(d.guidanceFor('What is your favorite color?').questionType).toBe('general');
  });
});

describe('Interview Answer Card', () => {
  // Built without the constructor: the card getter is pure view logic and
  // needs none of the injected services.
  const make = (g: AIInterviewResponse): InterviewComponent => {
    const c = Object.create(InterviewComponent.prototype) as InterviewComponent;
    c.guidance = g;
    return c;
  };

  it('groups Best Answer sentences into short paragraphs', () => {
    expect(groupSentences(['a.', 'b.', 'c.'])).toEqual(['a. b.', 'c.']);
    expect(groupSentences([])).toEqual([]);
  });

  it('builds the adaptive card for every demo answer', () => {
    for (const g of new DemoService().DEMO_GUIDANCE) {
      const card = make(g).card;
      expect(card.bestAnswer.length).toBeGreaterThan(0);
      expect(card.followUps.every((f) => !!f.question && !!f.answer)).toBeTrue();
      expect(card.isCoding).toBe(g.questionType === 'coding');
    }
  });

  it('labels question types for HR / theory / scenario / coding', () => {
    const base = { question: 'q', answerPoints: ['a'], followUpQuestions: [] };
    expect(make({ ...base, questionType: 'general' }).card.typeLabel).toBe('HR');
    expect(make({ ...base, questionType: 'technical' }).card.typeLabel).toBe('Theory');
    expect(make({ ...base, questionType: 'behavioral' }).card.typeLabel).toBe('Scenario');
  });

  it('still renders legacy payloads (keyTakeaways / followUpQuestions)', () => {
    const card = make({
      question: 'q',
      questionType: 'technical',
      answerPoints: ['a'],
      keyTakeaways: ['old takeaway'],
      followUpQuestions: ['Why? - Because it scales.', 'How?'],
    }).card;
    expect(card.keyDetails).toEqual(['old takeaway']);
    expect(card.followUps).toEqual([
      { question: 'Why?', answer: 'Because it scales.' },
      { question: 'How?', answer: '' },
    ]);
  });

  it('coding demo answers carry exactly ONE program (no human-style version)', () => {
    const coding = new DemoService().DEMO_GUIDANCE.find((g) => g.questionType === 'coding')!;
    expect(coding.codeHint).toBeTruthy();
    expect(coding.humanCode).toBeFalsy();
  });
});

describe('On-demand follow-ups ("Show follow-ups")', () => {
  const answer: AIInterviewResponse = {
    question: 'What is a saga?',
    questionType: 'technical',
    answerPoints: ['A saga splits a long transaction into local steps.'],
    followUps: [],
    followUpQuestions: [],
  };
  const make = (sendOk = true) => {
    const c = Object.create(InterviewComponent.prototype) as InterviewComponent;
    c.guidance = answer;
    c.followUpsLoading = false;
    c.followUpsError = '';
    const send = jasmine.createSpy('sendRequestFollowUps').and.returnValue(sendOk);
    (c as unknown as { ws: unknown }).ws = { sendRequestFollowUps: send };
    return { c, send };
  };

  it('answers arrive without follow-ups, so the card offers the button instead', () => {
    const { c } = make();
    expect(c.card.followUps).toEqual([]);
  });

  it('click requests follow-ups for the current question and shows loading', () => {
    const { c, send } = make();
    c.showFollowUps();
    expect(send).toHaveBeenCalledOnceWith('What is a saga?');
    expect(c.followUpsLoading).toBeTrue();
    c.showFollowUps(); // second click while loading is ignored
    expect(send).toHaveBeenCalledTimes(1);
  });

  it('merges the reply into the card (matching question, any case/spacing)', () => {
    const { c } = make();
    c.showFollowUps();
    c.applyFollowUps({ question: '  what is a SAGA? ', followUps: [{ question: 'Why not 2PC?', answer: 'It blocks.' }] });
    expect(c.followUpsLoading).toBeFalse();
    expect(c.card.followUps).toEqual([{ question: 'Why not 2PC?', answer: 'It blocks.' }]);
  });

  it('ignores a late reply for an answer no longer on screen', () => {
    const { c } = make();
    c.showFollowUps();
    c.applyFollowUps({ question: 'Some older question?', followUps: [{ question: 'x?', answer: 'y' }] });
    expect(c.followUpsLoading).toBeTrue();
    expect(c.card.followUps).toEqual([]);
  });

  it('shows the server error and allows a retry', () => {
    const { c, send } = make();
    c.showFollowUps();
    c.applyFollowUps({ question: 'What is a saga?', followUps: [], error: 'Could not load follow-ups. Try again.' });
    expect(c.followUpsLoading).toBeFalse();
    expect(c.followUpsError).toBe('Could not load follow-ups. Try again.');
    c.showFollowUps();
    expect(send).toHaveBeenCalledTimes(2);
  });

  it('reports a closed connection instead of spinning forever', () => {
    const { c } = make(false);
    c.showFollowUps();
    expect(c.followUpsLoading).toBeFalse();
    expect(c.followUpsError).toContain('Not connected');
  });
});

describe('ChatGPT-style list answers (numbered concepts + quick reference)', () => {
  const make = (g: AIInterviewResponse): InterviewComponent => {
    const c = Object.create(InterviewComponent.prototype) as InterviewComponent;
    c.guidance = g;
    return c;
  };
  const oop: AIInterviewResponse = {
    question: 'Can you explain the core OOP concepts in Java?',
    questionType: 'technical',
    answerPoints: [
      'Java has four core OOP concepts.',
      '**Encapsulation:** Wrapping data and methods in a class with `private` fields.',
      '**Inheritance:** One class acquires the members of another using `extends`.',
      '**Polymorphism**: One method, many forms via overloading and overriding.',
      '**Abstraction:** Exposing only essentials via `abstract` classes and `interface`s.',
      'Together they keep large codebases modular.',
    ],
    followUpQuestions: [],
    comparison: {
      headers: ['Concept', 'Key keyword'],
      rows: [['Encapsulation', '`private`'], ['Inheritance', '`extends`'], ['Polymorphism', '`@Override`'], ['Abstraction', '`interface`']],
    },
  };

  it('splits labelled points ("**Term:**" and "**Term**:") into numbered items', () => {
    const s = splitLabelled(oop.answerPoints);
    expect(s.intro).toEqual(['Java has four core OOP concepts.']);
    expect(s.items.length).toBe(4);
    expect(s.items[3]).toContain('Abstraction');
    expect(s.outro).toEqual(['Together they keep large codebases modular.']);
  });

  it('ordinary answers (fewer than 2 labelled points) keep paragraph style', () => {
    const s = splitLabelled(['Kafka is a log.', '**Topics** split data.', 'It scales.']);
    expect(s.items).toEqual([]);
    expect(s.intro.length).toBe(3);
  });

  it('builds a card with numbered items and the quick-reference table', () => {
    const card = make(oop).card;
    expect(card.bestItems.length).toBe(4);
    expect(card.bestIntro).toEqual(['Java has four core OOP concepts.']);
    expect(card.table?.rows.length).toBe(4);
  });

  it('ignores empty tables and never shows a table on coding answers', () => {
    expect(make({ ...oop, comparison: { headers: ['A'], rows: [] } }).card.table).toBeNull();
    expect(make({ ...oop, comparison: null }).card.table).toBeNull();
    const coding = { ...oop, questionType: 'coding' as const, codeHint: 'class A {}', approach: 'x' };
    expect(make(coding).card.table).toBeNull();
  });
});

describe('Auto Select Meeting Audio (web)', () => {
  const make = (audio: Record<string, jasmine.Spy | unknown>) => {
    const c = Object.create(InterviewComponent.prototype) as InterviewComponent;
    const ws = jasmine.createSpyObj('ws', ['sendInterviewerAudioStart']);
    Object.assign(c, { interviewerAudio: audio, ws, error: '', capturingInterviewer: false, isDesktop: false });
    spyOn(c as unknown as { startTimer: () => void }, 'startTimer');
    return { c, ws };
  };

  it('one click captures system audio through the loopback device (no share picker)', async () => {
    const audio = {
      status$: { value: '' },
      startSystemAudio: jasmine.createSpy().and.resolveTo(true),
      start: jasmine.createSpy(),
    };
    const { c, ws } = make(audio);
    await c.autoSelectMeetingAudio();
    expect(audio.startSystemAudio).toHaveBeenCalledTimes(1);
    // "Select meeting audio" (the picker) is a separate button — never reused here.
    expect(audio.start).not.toHaveBeenCalled();
    expect(ws.sendInterviewerAudioStart).toHaveBeenCalledOnceWith('audio/wav');
    expect(c.error).toBe('');
  });

  it('shows the setup helper instead of opening a picker when no loopback device is enabled', async () => {
    const audio = {
      status$: { value: 'Set up system audio, then click Auto Select again.' },
      startSystemAudio: jasmine.createSpy().and.resolveTo(false),
      start: jasmine.createSpy(),
    };
    const { c, ws } = make(audio);
    await c.autoSelectMeetingAudio();
    expect(audio.start).not.toHaveBeenCalled();
    expect(ws.sendInterviewerAudioStart).not.toHaveBeenCalled();
  });

  it('surfaces the specific reason when the device cannot be opened', async () => {
    const audio = {
      status$: { value: 'Microphone permission was denied' },
      startSystemAudio: jasmine.createSpy().and.rejectWith(new Error('permission denied')),
      start: jasmine.createSpy(),
    };
    const { c, ws } = make(audio);
    await c.autoSelectMeetingAudio();
    expect(audio.start).not.toHaveBeenCalled();
    expect(ws.sendInterviewerAudioStart).not.toHaveBeenCalled();
    expect(c.error).toBe('Microphone permission was denied');
  });

  it('ignores the click while already capturing', async () => {
    const audio = { status$: { value: '' }, startSystemAudio: jasmine.createSpy() };
    const { c, ws } = make(audio);
    c.capturingInterviewer = true;
    await c.autoSelectMeetingAudio();
    expect(audio.startSystemAudio).not.toHaveBeenCalled();
    expect(ws.sendInterviewerAudioStart).not.toHaveBeenCalled();
  });
});

describe('Session time limit (1h 15m)', () => {
  const make = (capturing: boolean) => {
    const c = Object.create(InterviewComponent.prototype) as InterviewComponent;
    Object.assign(c, { capturingInterviewer: capturing, error: '', elapsed: MAX_SESSION_SECONDS - 2, timer: null });
    const stop = spyOn(c, 'stopInterviewerAudio').and.resolveTo();
    const tick = () => (c as unknown as { tickTimer: () => void }).tickTimer();
    return { c, stop, tick };
  };

  it('auto-stops interviewer audio exactly at 01:15:00', () => {
    const { c, stop, tick } = make(true);
    tick();
    expect(stop).not.toHaveBeenCalled();
    tick();
    expect(c.elapsed).toBe(MAX_SESSION_SECONDS);
    expect(stop).toHaveBeenCalledTimes(1);
    expect(c.error).toContain('1h 15m');
  });

  it('never counts past the cap even when no audio is capturing', () => {
    const { c, stop, tick } = make(false);
    tick(); tick(); tick();
    expect(c.elapsed).toBe(MAX_SESSION_SECONDS);
    expect(stop).not.toHaveBeenCalled();
  });
});

describe('Transcript pause / resume', () => {
  const make = (capturing: boolean) => {
    const c = Object.create(InterviewComponent.prototype) as InterviewComponent;
    const audio = jasmine.createSpyObj('audio', ['setPaused']);
    const ws = jasmine.createSpyObj('ws', ['setStatus']);
    // The service's paused$ normally drives audioPaused; mirror it here.
    audio.setPaused.and.callFake((p: boolean) => (c.audioPaused = p));
    Object.assign(c, {
      interviewerAudio: audio, ws, capturingInterviewer: capturing, audioPaused: false,
      clipQueue: [{}], streamHold: null, liveTyped: { set: () => undefined },
    });
    return { c, audio, ws };
  };

  it('pauses STT, drops queued audio, then resumes on the second click', () => {
    const { c, audio, ws } = make(true);
    c.toggleAudioPause();
    expect(audio.setPaused).toHaveBeenCalledWith(true);
    expect(ws.setStatus).toHaveBeenCalledWith('paused');
    expect((c as unknown as { clipQueue: unknown[] }).clipQueue.length).toBe(0);
    c.toggleAudioPause();
    expect(audio.setPaused).toHaveBeenCalledWith(false);
    expect(ws.setStatus).toHaveBeenCalledWith('listening');
    expect(c.audioPaused).toBeFalse();
  });

  it('does nothing when meeting audio is not on', () => {
    const { c, audio } = make(false);
    c.toggleAudioPause();
    expect(audio.setPaused).not.toHaveBeenCalled();
  });
});

describe('Auto Select Meeting Audio (desktop .exe)', () => {
  const make = (audio: Record<string, unknown>) => {
    const c = Object.create(InterviewComponent.prototype) as InterviewComponent;
    const ws = jasmine.createSpyObj('ws', ['sendInterviewerAudioStart']);
    Object.assign(c, { interviewerAudio: audio, ws, error: '', isDesktop: true });
    spyOn(c as unknown as { startTimer: () => void }, 'startTimer');
    return { c, ws };
  };

  it('captures Windows loopback directly, without the Stereo Mix lookup', async () => {
    const audio = {
      status$: { value: '' },
      startDesktopLoopbackAudio: jasmine.createSpy().and.resolveTo(true),
      startSystemAudio: jasmine.createSpy(),
    };
    const { c, ws } = make(audio);
    await c.startSystemAudio();
    expect(audio.startSystemAudio).not.toHaveBeenCalled();
    expect(ws.sendInterviewerAudioStart).toHaveBeenCalledOnceWith('audio/wav');
  });

  it('falls back to a loopback input device when no loopback audio came back', async () => {
    const audio = {
      status$: { value: '' },
      startDesktopLoopbackAudio: jasmine.createSpy().and.resolveTo(false),
      startSystemAudio: jasmine.createSpy().and.resolveTo(true),
    };
    const { c, ws } = make(audio);
    await c.startSystemAudio();
    expect(audio.startSystemAudio).toHaveBeenCalled();
    expect(ws.sendInterviewerAudioStart).toHaveBeenCalledTimes(1);
  });
});

describe('AI Guidance "Earlier this session"', () => {
  const item = (n: number) =>
    ({ question: `Q${n}`, guidance: { question: `Q${n}`, questionType: 'technical', answerPoints: ['a'] } }) as never;

  it('shows at most 4 past answers, newest first, excluding the current one', () => {
    const c = Object.create(InterviewComponent.prototype) as InterviewComponent;
    c.history = [1, 2, 3, 4, 5, 6, 7].map(item);
    expect(MAX_PAST_HISTORY).toBe(4);
    // Q7 is the current answer card; Q6..Q3 are the 4 newest earlier ones.
    expect(c.pastHistory.map((h) => h.question)).toEqual(['Q6', 'Q5', 'Q4', 'Q3']);
  });

  it('shows fewer when the session has fewer past answers', () => {
    const c = Object.create(InterviewComponent.prototype) as InterviewComponent;
    c.history = [1, 2, 3].map(item);
    expect(c.pastHistory.map((h) => h.question)).toEqual(['Q2', 'Q1']);
    c.history = [item(1)];
    expect(c.pastHistory).toEqual([]);
  });
});

describe('Live interviewer transcript (meeting audio)', () => {
  // Before: the Transcript line stayed empty until the interviewer finished
  // the whole sentence, then updated in ~2s jumps. Text now streams in
  // word-by-word (typewriter-smoothed) — but provisional text must never act
  // like a final transcript.
  beforeEach(() => jasmine.clock().install());
  afterEach(() => jasmine.clock().uninstall());

  const make = () => {
    const c = Object.create(InterviewComponent.prototype) as InterviewComponent;
    Object.assign(c, {
      interim: '',
      interviewerInterim: '',
      interimSeq: -1,
      streamText: '',
      streamOpen: false,
      streamHold: null,
      liveTyped: new SmoothText(),
      lastInterviewerText: 'Previous question?',
    });
    const release = spyOn(c as unknown as { releaseClipSlot: () => void }, 'releaseClipSlot');
    return { c, release };
  };
  /** Let the typewriter finish revealing. */
  const typed = (c: InterviewComponent) => {
    jasmine.clock().tick(1_000);
    return c.liveTranscript;
  };

  it('shows provisional text live without finalising it or freeing the clip slot', () => {
    const { c, release } = make();
    c.onInterviewerTranscript({ text: 'Can you explain', final: false, seq: 0 });
    expect(typed(c)).toBe('Can you explain');
    c.onInterviewerTranscript({ text: 'Can you explain the Saga', final: false, seq: 0 });
    expect(typed(c)).toBe('Can you explain the Saga');
    expect(c.lastInterviewerText).toBe('Previous question?');
    expect(release).not.toHaveBeenCalled();
  });

  it('the final transcript replaces its own clip’s live text', () => {
    const { c, release } = make();
    c.onInterviewerTranscript({ text: 'Can you explain the Saga', final: false, seq: 0 });
    c.onInterviewerTranscript({ text: 'Can you explain the Saga pattern?', final: true, seq: 0 });
    expect(typed(c)).toBe('');
    expect(c.lastInterviewerText).toBe('Can you explain the Saga pattern?');
    expect(release).toHaveBeenCalledTimes(1);
  });

  it('a final arriving mid-typing glides into the final words instead of jumping', () => {
    const { c, release } = make();
    c.onInterviewerTranscript({ text: 'Explain the Java', final: false, seq: 0 });
    typed(c);
    c.onInterviewerTranscript({ text: 'Explain the Java collection framework', final: false, seq: 0 });
    jasmine.clock().tick(30); // typing the new words
    c.onInterviewerTranscript({ text: 'Explain the Java collection framework hierarchy.', final: true, seq: 0 });
    // Still the live line, still typing — no jump to the full final text.
    const mid = c.liveTranscript;
    expect(mid.length).toBeGreaterThan('Explain the Java'.length - 1);
    expect(mid.length).toBeLessThan('Explain the Java collection framework hierarchy.'.length);
    expect(release).toHaveBeenCalledTimes(1); // the answer slot is freed at once
    // Once fully typed, the line hands over to the final transcript.
    expect(typed(c)).toBe('');
    expect(c.lastInterviewerText).toBe('Explain the Java collection framework hierarchy.');
  });

  it('provisional snapshots never flicker a closing full stop in and out', () => {
    const { c } = make();
    c.onInterviewerTranscript({ text: 'Explain the Java.', final: false, seq: 0 });
    expect(typed(c)).toBe('Explain the Java');
    let shrank = false;
    let last = c.liveTranscript.length;
    c.onInterviewerTranscript({ text: 'Explain the Java collection framework.', final: false, seq: 0 });
    for (let i = 0; i < 40; i++) {
      jasmine.clock().tick(30);
      if (c.liveTranscript.length < last) shrank = true;
      last = c.liveTranscript.length;
    }
    expect(shrank).toBeFalse(); // only ever grows — no backing up over a "."
    expect(c.liveTranscript).toBe('Explain the Java collection framework');
  });

  it('a late final of an older clip keeps the newer clip’s live text', () => {
    const { c } = make();
    c.onInterviewerTranscript({ text: 'And how does', final: false, seq: 1 });
    c.onInterviewerTranscript({ text: 'What is a saga?', final: true, seq: 0 });
    expect(typed(c)).toBe('And how does');
    // ...and an out-of-date preview never overwrites a newer one.
    c.onInterviewerTranscript({ text: 'What is', final: false, seq: 0 });
    expect(typed(c)).toBe('And how does');
  });

  it('typed questions (no seq) still finalise and clear live text', () => {
    const { c } = make();
    c.onInterviewerTranscript({ text: 'Tell me', final: false, seq: 3 });
    c.onInterviewerTranscript({ text: 'Tell me about yourself.', final: true });
    expect(typed(c)).toBe('');
    expect(c.lastInterviewerText).toBe('Tell me about yourself.');
  });

  it('microphone interim text still takes priority', () => {
    const { c } = make();
    c.interim = 'mic words';
    c.onInterviewerTranscript({ text: 'meeting words', final: false, seq: 0 });
    expect(typed(c)).toBe('mic words');
  });

  it('streamed words appear progressively, not in one jump', () => {
    const { c, release } = make();
    c.onLiveText({ text: 'Can you explain the difference between', final: false });
    jasmine.clock().tick(30);
    const early = c.liveTranscript;
    expect(early.length).toBeGreaterThan(0);
    expect(early.length).toBeLessThan('Can you explain the difference between'.length);
    expect(typed(c)).toBe('Can you explain the difference between');
    // Display only: no answer slot touched, final text untouched.
    expect(release).not.toHaveBeenCalled();
    expect(c.lastInterviewerText).toBe('Previous question?');
  });

  it('a pause mid-question keeps the open utterance growing past the clip final', () => {
    const { c } = make();
    c.onLiveText({ text: 'Can you explain the Saga pattern', final: false });
    c.onInterviewerTranscript({ text: 'Can you explain the Saga pattern', final: true, seq: 0 });
    c.onLiveText({ text: 'Can you explain the Saga pattern and two-phase commit', final: false });
    expect(typed(c)).toBe('Can you explain the Saga pattern and two-phase commit');
  });

  it('a finished utterance yields to the final transcript (or a timeout)', () => {
    const { c } = make();
    c.onLiveText({ text: 'What is a saga?', final: true });
    expect(typed(c)).toBe('What is a saga?');
    c.onInterviewerTranscript({ text: 'What is a saga?', final: true, seq: 0 });
    expect(typed(c)).toBe('');

    c.onLiveText({ text: 'Hello there', final: true }); // no clip final ever comes
    jasmine.clock().tick(3_000);
    expect(c.liveTranscript).toBe('');
  });
});

describe('Only the interviewer is heard (never the candidate mic)', () => {
  // Runs the real ngOnInit against stubs: every `x$` is a silent stream and
  // every method a spy returning an empty observable.
  const stub = (extra: Record<string, unknown> = {}) =>
    new Proxy(extra, {
      get: (t, k: string) =>
        k in t ? t[k] : (t[k] = k.endsWith('$') ? new Subject() : jasmine.createSpy(k).and.returnValue(EMPTY)),
    });

  it('page load does not start the Web Speech (microphone) recognizer', () => {
    const c = Object.create(InterviewComponent.prototype) as InterviewComponent;
    const transcription = stub({ isSupported: () => true });
    Object.assign(c, {
      subs: [],
      route: { snapshot: { queryParamMap: { get: () => null } } },
      settings: { value: {} },
      ws: stub(),
      transcription,
      resumeService: stub(),
      detector: stub(),
      interviewerAudio: stub(),
    });
    c.ngOnInit();
    expect((transcription as unknown as { start: jasmine.Spy }).start).not.toHaveBeenCalled();
    expect(c.micSupported).toBeTrue();
  });
});

describe('Interview free trial', () => {
  const make = (): InterviewComponent => Object.create(InterviewComponent.prototype) as InterviewComponent;

  it('rounds the minutes left up so the last seconds still show 1 min', () => {
    const c = make();
    c.liveAccess = { mode: 'trial', client: 'web', remainingSeconds: 61, limitSeconds: 1200 };
    expect(c.trialMinutesLeft).toBe(2);
    c.liveAccess = { mode: 'trial', client: 'web', remainingSeconds: 1, limitSeconds: 1200 };
    expect(c.trialMinutesLeft).toBe(1);
  });

  it('does not start meeting audio once the trial is used up', async () => {
    const c = make();
    let started = false;
    (c as unknown as { interviewerAudio: { start: () => Promise<void> } }).interviewerAudio = {
      start: async () => void (started = true),
    };
    c.liveAccess = { mode: 'denied', client: 'web', remainingSeconds: 0, limitSeconds: 1200 };
    await c.startInterviewerAudio();
    expect(started).toBeFalse();
  });
});

describe('InterviewComponent clip queue', () => {
  type Q = {
    enqueueClip(c: { data: ArrayBuffer; mimeType: string }): void;
    clipQueue: unknown[];
    audioBusy: boolean;
  };
  const clip = (n: number) => ({ data: new ArrayBuffer(n), mimeType: 'audio/wav' });
  const make = () => {
    const c = Object.create(InterviewComponent.prototype) as InterviewComponent;
    const sent: number[] = [];
    Object.assign(c, {
      clipQueue: [],
      audioBusy: false,
      clipSlotTimer: null,
      thinking: false,
      audioStatus: '',
      responseLength: 'short',
      ws: {
        isConnected: () => true,
        sendAudioClip: (data: ArrayBuffer) => sent.push(data.byteLength),
      },
    });
    return { c, q: c as unknown as Q, sent, release: () => (c as unknown as { releaseClipSlot(): void }).releaseClipSlot() };
  };

  beforeEach(() => jasmine.clock().install());
  afterEach(() => jasmine.clock().uninstall());

  // Regression: a newer clip used to REPLACE the queued one, so the middle of
  // a question ("Can you explain the difference between") never reached STT.
  it('keeps every clip that arrives while one is in flight, in order', () => {
    const { q, sent, release } = make();
    q.enqueueClip(clip(1));
    q.enqueueClip(clip(2));
    q.enqueueClip(clip(3));
    expect(sent).toEqual([1]);
    release();
    release();
    expect(sent).toEqual([1, 2, 3]);
  });

  it('a stale safety timer never frees the next clip’s slot early', () => {
    const { q, sent, release } = make();
    q.enqueueClip(clip(1));
    jasmine.clock().tick(9_000);
    release(); // clip 1 answered at 9 s; clip 2 is not queued yet
    q.enqueueClip(clip(2));
    q.enqueueClip(clip(3));
    jasmine.clock().tick(2_000); // clip 1's old 10 s timer would fire here
    expect(sent).toEqual([1, 2]);
    expect(q.audioBusy).toBeTrue();
    jasmine.clock().tick(10_000); // clip 2's own timeout
    expect(sent).toEqual([1, 2, 3]);
  });
});

describe('Reconnect during a call', () => {
  // Regression: after a backend restart / network blip the new socket had
  // capture OFF, so every audio frame was dropped and the Transcript froze on
  // "Listening for the next question…" for the rest of the call.
  const make = (capturing: boolean) => {
    const c = Object.create(InterviewComponent.prototype) as InterviewComponent;
    const calls: string[] = [];
    Object.assign(c, {
      capturingInterviewer: capturing,
      clipQueue: [{ data: new ArrayBuffer(4), mimeType: 'audio/wav' }],
      audioBusy: true, // its clip died with the old socket
      clipSlotTimer: null,
      thinking: false,
      audioStatus: '',
      responseLength: 'short',
      streamHold: null,
      streamText: 'Can you explain',
      streamOpen: true,
      interviewerInterim: '',
      liveTyped: new SmoothText(),
      ws: {
        isConnected: () => true,
        sendInterviewerAudioStart: () => calls.push('start'),
        sendAudioClip: () => calls.push('clip'),
      },
    });
    return { resume: () => (c as unknown as { resumeCaptureOnServer(): void }).resumeCaptureOnServer(), calls };
  };

  it('re-announces the running capture before any queued clip', () => {
    const { resume, calls } = make(true);
    resume();
    expect(calls).toEqual(['start', 'clip']);
  });

  it('does nothing when no capture is running', () => {
    const { resume, calls } = make(false);
    resume();
    expect(calls).toEqual([]);
  });
});
