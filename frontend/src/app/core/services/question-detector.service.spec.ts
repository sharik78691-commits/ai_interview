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
});
