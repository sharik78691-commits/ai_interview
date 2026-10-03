import { TranscriptionService } from './transcription.service';

describe('TranscriptionService smoke', () => {
  it('emits manual transcripts', (done) => {
    const svc = new TranscriptionService();
    svc.transcript$.subscribe((t) => {
      expect(t.text).toBe('hello world');
      expect(t.isFinal).toBeTrue();
      done();
    });
    svc.pushManual('hello world');
  });

  it('ignores blank manual input', (done) => {
    const svc = new TranscriptionService();
    let count = 0;
    svc.transcript$.subscribe(() => count++);
    svc.pushManual('   ');
    setTimeout(() => {
      expect(count).toBe(0);
      done();
    }, 50);
  });
});
