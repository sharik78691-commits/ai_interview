import { DemoService } from '../../core/services/demo.service';

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
