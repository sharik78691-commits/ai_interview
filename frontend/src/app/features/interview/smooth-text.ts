/**
 * Typewriter smoothing for live transcript text.
 *
 * Live STT delivers text in bursts: the Gemini Live stream every ~0.5 s, the
 * snapshot previews (admin STT, e.g. Whisper on DeepInfra) every ~0.7–1.8 s.
 * Rendering each burst as-is reads as "stuck, then jumps". This reveals new
 * characters over the time until the NEXT burst is expected (learned from the
 * gaps between bursts), so the line keeps growing at a steady pace like a
 * voice assistant's live captions instead of typing a burst in 150 ms and then
 * standing still for a second.
 *
 * Corrections never retype: when the provider revises earlier words
 * ("differencebetween" -> "difference between"), the already-shown span is
 * swapped in place and only genuinely new text types in.
 */
/** Code units that belong to the character before them: combining marks
 * (Devanagari vowel signs, viramas), zero-width joiners, low surrogates. */
const JOINS_PREVIOUS = /[\p{M}‌‍\uDC00-\uDFFF]/u;
/** Devanagari virama: the consonant after it is part of the same conjunct. */
const VIRAMA = '्';

/**
 * Move a cut forward to the end of the character it lands in. Slicing
 * "क्या" by code units showed "क", then "क्", then "क्य" while typing — each a
 * different, wrong letter shape. ASCII is unaffected.
 */
export function clusterEnd(text: string, end: number): number {
  while (
    end > 0 &&
    end < text.length &&
    (JOINS_PREVIOUS.test(text[end]) || text[end - 1] === VIRAMA || text[end - 1] === '‍')
  ) {
    end++;
  }
  return end;
}

export class SmoothText {
  /** What the view should render right now. */
  shown = '';
  private target = '';
  private timer: ReturnType<typeof setTimeout> | null = null;
  /** Characters revealed per tick (re-planned on every new burst). */
  private step = 2;
  /** Smoothed gap between growing bursts (0 = not learned yet). */
  private gapMs = 0;
  private lastGrowAt = 0;
  private onDone: (() => void) | null = null;

  /**
   * @param tickMs    delay between reveal steps
   * @param catchUp   ticks to finish a burst before the burst gap is known
   * @param maxSpreadMs  never spread one burst over longer than this, so the
   *                  line cannot fall far behind the speaker
   */
  constructor(
    private readonly tickMs = 30,
    private readonly catchUp = 5,
    private readonly maxSpreadMs = 900,
  ) {}

  set(target: string): void {
    if (!target) {
      this.stop();
      this.target = '';
      this.shown = '';
      this.gapMs = 0;
      this.lastGrowAt = 0;
      this.done();
      return;
    }
    const grew = target.length > this.target.length;
    this.target = target;
    if (!target.startsWith(this.shown)) {
      this.shown = target.slice(0, clusterEnd(target, Math.min(this.shown.length, target.length)));
    }
    if (grew) this.learnGap();
    this.plan();
    if (this.shown.length >= target.length) this.done();
    else if (!this.timer) this.schedule();
  }

  /** Jump to the end (e.g. when the text is final). */
  flush(): void {
    this.stop();
    this.shown = this.target;
    this.done();
  }

  /** Run ``cb`` once the current text is fully revealed (now, if it already is). */
  finish(cb: () => void): void {
    if (this.shown.length >= this.target.length) {
      cb();
      return;
    }
    this.onDone = cb;
  }

  stop(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  /** Spread what is still hidden over the expected time to the next burst. */
  private plan(): void {
    const left = this.target.length - this.shown.length;
    if (left <= 0) return;
    const spread = this.gapMs
      ? // 60% of the gap: words still flow, but reach the screen sooner (the
        // whole spread is extra lag behind the speaker).
        Math.min(this.maxSpreadMs, Math.max(this.tickMs * this.catchUp, this.gapMs * 0.6))
      : this.tickMs * this.catchUp;
    const ticks = Math.max(1, Math.round(spread / this.tickMs));
    this.step = Math.max(1, Math.ceil(left / ticks));
  }

  private learnGap(): void {
    const now = Date.now();
    const gap = now - this.lastGrowAt;
    // Bursts of one utterance arrive well under 3 s apart; a longer gap is a
    // new sentence and says nothing about the cadence.
    if (this.lastGrowAt && gap >= this.tickMs && gap < 3_000) {
      this.gapMs = this.gapMs ? this.gapMs * 0.6 + gap * 0.4 : gap;
    }
    this.lastGrowAt = now;
  }

  private done(): void {
    const cb = this.onDone;
    this.onDone = null;
    cb?.();
  }

  private schedule(): void {
    this.timer = setTimeout(() => this.tick(), this.tickMs);
  }

  private tick(): void {
    this.timer = null;
    if (this.shown.length >= this.target.length) return this.done();
    this.shown = this.target.slice(0, clusterEnd(this.target, this.shown.length + this.step));
    if (this.shown.length < this.target.length) this.schedule();
    else this.done();
  }
}
