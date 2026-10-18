// Global `window.electronAPI` typing the component references (types only).
import '../../core/services/electron-bridge';
import { AIInterviewResponse } from '../../core/models/interview.models';
import { DemoService } from '../../core/services/demo.service';
import { InterviewComponent, groupSentences } from './interview.component';

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

  it('coding answers carry two programs: AI and human-style', () => {
    const coding = new DemoService().DEMO_GUIDANCE.find((g) => g.questionType === 'coding')!;
    expect(coding.codeHint).toBeTruthy();
    expect(coding.humanCode).toBeTruthy();
    expect(coding.humanCode).not.toEqual(coding.codeHint);
    const text = make(coding).answerText;
    expect(text).toContain('Program 1 (AI solution):');
    expect(text).toContain('Program 2 (human-style version):');
  });

  it('copy text follows the card, without the removed fixed sections', () => {
    const text = make(new DemoService().DEMO_GUIDANCE[0]).answerText;
    expect(text).toContain('Best answer:');
    expect(text).toContain('If they ask further:');
    expect(text).not.toContain('Key takeaways');
    expect(text).not.toContain('Code hint');
  });
});
