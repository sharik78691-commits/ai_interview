import { isWav, normalizePeak, pcmToWav, trimSilence } from './wav-encoder';

const tone = (n: number, amp: number): Float32Array =>
  Float32Array.from({ length: n }, (_, i) => amp * Math.sin(i * 0.3));

describe('wav-encoder', () => {
  const RATE = 48_000;

  it('trims leading/trailing silence but keeps the voiced part', () => {
    const lead = new Float32Array(RATE); // 1 s quiet
    const speech = tone(RATE / 2, 0.3); // 0.5 s voiced
    const tail = new Float32Array(RATE);
    const all = new Float32Array(lead.length + speech.length + tail.length);
    all.set(lead, 0);
    all.set(speech, lead.length);
    all.set(tail, lead.length + speech.length);

    const trimmed = trimSilence(all, RATE, 0.012);
    expect(trimmed.length).toBeLessThan(all.length / 2);
    expect(trimmed.length).toBeGreaterThanOrEqual(speech.length);
  });

  it('returns an all-silent clip untouched', () => {
    const quiet = new Float32Array(RATE);
    expect(trimSilence(quiet, RATE, 0.012)).toBe(quiet);
  });

  it('boosts quiet audio with a capped gain and leaves loud audio alone', () => {
    const quiet = tone(1000, 0.05);
    const boosted = normalizePeak(quiet);
    const peak = Math.max(...boosted.map(Math.abs));
    expect(peak).toBeGreaterThan(0.2);
    expect(peak).toBeLessThanOrEqual(0.31); // 6x cap on a 0.05 peak
    const loud = tone(1000, 0.95);
    expect(normalizePeak(loud)).toBe(loud);
  });

  it('encodes a valid 16 kHz mono WAV, smaller after trimming', () => {
    const chunks = [new Float32Array(RATE), tone(RATE / 2, 0.3), new Float32Array(RATE)];
    const raw = pcmToWav(chunks, RATE);
    const trimmed = pcmToWav(chunks, RATE, undefined, 0.012);
    expect(isWav(trimmed)).toBeTrue();
    expect(trimmed.byteLength).toBeLessThan(raw.byteLength / 2);
    const view = new DataView(trimmed);
    expect(view.getUint32(24, true)).toBe(16_000);
    expect(view.getUint16(22, true)).toBe(1);
  });
});
