import { QuestionDetectorService } from './question-detector.service';

describe('QuestionDetectorService', () => {
  it('detects questions with ?', () => {
    expect(QuestionDetectorService.detect('What is a saga?')).toBeTrue();
  });

  it('detects wh-questions without ?', () => {
    expect(QuestionDetectorService.detect('Explain how hashing works please')).toBeTrue();
  });

  it('rejects short statements', () => {
    expect(QuestionDetectorService.detect('Hi there')).toBeFalse();
  });

  it('rejects plain statements', () => {
    expect(QuestionDetectorService.detect('Thanks for joining the call today everyone')).toBeFalse();
  });

  // Regression: String.includes matched a question word ANYWHERE, so
  // "show", "somewhere" and "whichever" looked like "how"/"where"/"which"
  // and each fired a needless answer.
  it('does not match a question word inside another word', () => {
    expect(QuestionDetectorService.detect('Please show me the final design')).toBeFalse();
    expect(QuestionDetectorService.detect('We discussed this somewhere else already')).toBeFalse();
    expect(QuestionDetectorService.detect('Whichever option you prefer is fine')).toBeFalse();
    expect(QuestionDetectorService.detect('There is some weather today outside')).toBeFalse();
  });

  it('still matches real question words as whole words', () => {
    expect(QuestionDetectorService.detect('How do you handle a slow query')).toBeTrue();
    expect(QuestionDetectorService.detect('Explain the difference between a thread and a process')).toBeTrue();
    expect(QuestionDetectorService.detect('Tell me about your last role at the company')).toBeTrue();
  });

  // Regression: the list was English-only, so Hinglish/Hindi prompts — which
  // is what most interviewers here actually speak — were never flagged.
  // Hindi is wh-in-situ, so the cue stays in place ("java mein kya hota hai").
  it('detects Hinglish and Hindi questions', () => {
    expect(
      QuestionDetectorService.detect('bhai hashmap aur concurrent hashmap ka kya difference hai matlab java mein'),
    ).toBeTrue();
    expect(QuestionDetectorService.detect('spring boot ke baare mein batao')).toBeTrue();
    expect(QuestionDetectorService.detect('multithreading kaise kaam karta hai')).toBeTrue();
    expect(QuestionDetectorService.detect('kya hashmap thread safe hai')).toBeTrue();
    expect(QuestionDetectorService.detect('स्प्रिंग बूट के बारे में बताइए')).toBeTrue();
  });

  it('still rejects Hinglish small talk', () => {
    expect(QuestionDetectorService.detect('acha chalo shuru karte hain')).toBeFalse();
    expect(QuestionDetectorService.detect('haan theek hai bahut badhiya')).toBeFalse();
  });
});
