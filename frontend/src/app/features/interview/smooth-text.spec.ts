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
