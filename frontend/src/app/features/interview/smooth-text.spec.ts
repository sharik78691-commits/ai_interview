import { SmoothText } from './smooth-text';

describe('SmoothText (live transcript typewriter)', () => {
  beforeEach(() => jasmine.clock().install());
  afterEach(() => jasmine.clock().uninstall());

  it('reveals a burst over a few ticks and finishes well before the next burst', () => {
    const t = new SmoothText(30, 5);
    t.set('Can you explain the difference between');
    expect(t.shown).toBe('');
    jasmine.clock().tick(30);
    expect(t.shown.length).toBeGreaterThan(0);
    // Streaming STT sends a new burst ~every 500 ms: typing must be done by then.
    jasmine.clock().tick(400);
    expect(t.shown).toBe('Can you explain the difference between');
  });

  it('only types the NEW words when the text grows', () => {
    const t = new SmoothText();
    t.set('Can you');
    t.flush();
    t.set('Can you explain');
    expect(t.shown).toBe('Can you');
    jasmine.clock().tick(1_000);
    expect(t.shown).toBe('Can you explain');
  });

  it('applies a provider correction in place instead of retyping', () => {
    const t = new SmoothText();
    t.set('Can you explain the differencebetween');
    t.flush();
    t.set('Can you explain the difference between the');
    // The corrected span is swapped in place at the same length; nothing is erased.
    expect(t.shown.length).toBe('Can you explain the differencebetween'.length);
    expect('Can you explain the difference between the'.startsWith(t.shown)).toBeTrue();
    jasmine.clock().tick(1_000);
    expect(t.shown).toBe('Can you explain the difference between the');
  });

  it('spreads each burst over the learned gap so the line keeps moving', () => {
    // Before: a burst typed in ~150 ms, then the line stood still until the
    // next snapshot (~0.8 s later) — "jump, wait, jump".
    jasmine.clock().mockDate(new Date(2026, 9, 29));
    const t = new SmoothText();
    t.set('Explain the Java');
    t.flush();
    jasmine.clock().tick(800);
    t.set('Explain the Java collection framework'); // second burst, 800 ms later
    jasmine.clock().tick(300);
    const mid = t.shown.length;
    expect(mid).toBeGreaterThan('Explain the Java'.length); // already moving...
    expect(mid).toBeLessThan('Explain the Java collection framework'.length); // ...not dumped at once
    jasmine.clock().tick(500);
    expect(t.shown).toBe('Explain the Java collection framework'); // done before the next burst
  });

  it('never spreads a burst longer than maxSpreadMs (cannot fall far behind)', () => {
    jasmine.clock().mockDate(new Date(2026, 9, 29));
    const t = new SmoothText(30, 5, 1_200);
    t.set('Tell me');
    t.flush();
    jasmine.clock().tick(2_500);
    t.set('Tell me about a time you led a team');
    jasmine.clock().tick(1_250);
    expect(t.shown).toBe('Tell me about a time you led a team');
  });

  it('finish() runs once the text is fully revealed', () => {
    const t = new SmoothText();
    const done = jasmine.createSpy('done');
    t.set('Hello world');
    t.finish(done);
    expect(done).not.toHaveBeenCalled();
    jasmine.clock().tick(1_000);
    expect(done).toHaveBeenCalledTimes(1);
    t.finish(done); // already revealed: immediately
    expect(done).toHaveBeenCalledTimes(2);
  });

  it('clears instantly', () => {
    const t = new SmoothText();
    t.set('Hello world');
    jasmine.clock().tick(30);
    t.set('');
    expect(t.shown).toBe('');
    jasmine.clock().tick(1_000);
    expect(t.shown).toBe('');
  });
});
