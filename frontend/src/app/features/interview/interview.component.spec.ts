// Global `window.electronAPI` typing the component references (types only).
import '../../core/services/electron-bridge';
import { AIInterviewResponse } from '../../core/models/interview.models';
import { DemoService } from '../../core/services/demo.service';
import { InterviewComponent, groupSentences, splitLabelled } from './interview.component';

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

  it('coding answers carry exactly ONE program (no human-style version)', () => {
    const coding = new DemoService().DEMO_GUIDANCE.find((g) => g.questionType === 'coding')!;
    expect(coding.codeHint).toBeTruthy();
    expect(coding.humanCode).toBeFalsy();
    const text = make(coding).answerText;
    expect(text).toContain('Program:');
    expect(text).not.toContain('Program 1');
    expect(text).not.toContain('Program 2');
    expect(text).not.toContain('human-style');
    // Even an old stored answer that still carries humanCode shows one program.
    expect(make({ ...coding, humanCode: 'def x(): pass' }).answerText).not.toContain('def x(): pass');
  });

  it('copy text follows the card, without the removed fixed sections', () => {
    const text = make(new DemoService().DEMO_GUIDANCE[0]).answerText;
    expect(text).toContain('Best answer:');
    expect(text).toContain('If they ask further:');
    expect(text).not.toContain('Key takeaways');
    expect(text).not.toContain('Code hint');
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
    expect(c.answerText).toContain('Why not 2PC?');
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

  it('copy text numbers the concepts and includes the table', () => {
    const text = make(oop).answerText;
    expect(text).toContain('1. **Encapsulation:**');
    expect(text).toContain('4. **Abstraction:**');
    expect(text).toContain('Quick reference:');
    expect(text).toContain('Abstraction | `interface`');
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
    Object.assign(c, { interviewerAudio: audio, ws, error: '', capturingInterviewer: false });
    spyOn(c as unknown as { startTimer: () => void }, 'startTimer');
    return { c, ws };
  };
  const status = { value: 'boom' };

  it('one click captures a loopback device and starts the question stream', async () => {
    const audio = {
      status$: status,
      startSystemAudio: jasmine.createSpy().and.resolveTo(true),
      startScreenSystemAudio: jasmine.createSpy(),
    };
    const { c, ws } = make(audio);
    await c.autoSelectMeetingAudio();
    expect(audio.startScreenSystemAudio).not.toHaveBeenCalled();
    expect(ws.sendInterviewerAudioStart).toHaveBeenCalledOnceWith('audio/wav');
  });

  it('falls back to screen system audio when no loopback device exists', async () => {
    const audio = {
      status$: status,
      startSystemAudio: jasmine.createSpy().and.resolveTo(false),
      startScreenSystemAudio: jasmine.createSpy().and.resolveTo(undefined),
    };
    const { c, ws } = make(audio);
    await c.autoSelectMeetingAudio();
    expect(audio.startScreenSystemAudio).toHaveBeenCalled();
    expect(ws.sendInterviewerAudioStart).toHaveBeenCalledOnceWith('audio/wav');
    expect(c.error).toBe('');
  });

  it('falls back to screen audio when the mic-permission prompt is declined', async () => {
    const audio = {
      status$: status,
      startSystemAudio: jasmine.createSpy().and.rejectWith(new Error('permission denied')),
      startScreenSystemAudio: jasmine.createSpy().and.resolveTo(undefined),
    };
    const { c, ws } = make(audio);
    await c.autoSelectMeetingAudio();
    expect(ws.sendInterviewerAudioStart).toHaveBeenCalled();
  });

  it('does not start the stream when the user cancels the share', async () => {
    const audio = {
      status$: status,
      startSystemAudio: jasmine.createSpy().and.resolveTo(false),
      startScreenSystemAudio: jasmine.createSpy().and.rejectWith(new Error('cancel')),
    };
    const { c, ws } = make(audio);
    await c.autoSelectMeetingAudio();
    expect(ws.sendInterviewerAudioStart).not.toHaveBeenCalled();
    expect(c.error).toBe('boom');
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
