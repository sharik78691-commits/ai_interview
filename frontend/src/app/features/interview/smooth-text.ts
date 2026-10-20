/**
 * Typewriter smoothing for live transcript text.
 *
 * Streaming STT delivers text in bursts (~every 0.5 s, 1–3 words at a time).
 * Rendering each burst as-is reads as "stuck, then jumps". This reveals new
 * characters over a few short ticks instead, so the line grows continuously,
 * like a voice assistant's live captions.
 *
 * Corrections never retype: when the provider revises earlier words
 * ("differencebetween" -> "difference between"), the already-shown span is
 * swapped in place and only genuinely new text types in.
 */
export class SmoothText {
  /** What the view should render right now. */
  shown = '';
  private target = '';
  private timer: ReturnType<typeof setTimeout> | null = null;

  /**
   * @param tickMs   delay between reveal steps
   * @param catchUp  the remaining gap is closed in roughly this many ticks,
   *                 so a burst finishes typing well before the next arrives
   */
  constructor(
    private readonly tickMs = 30,
    private readonly catchUp = 5,
  ) {}

  set(target: string): void {
    this.target = target;
    if (!target) {
      this.stop();
      this.shown = '';
      return;
    }
    if (!target.startsWith(this.shown)) {
      this.shown = target.slice(0, Math.min(this.shown.length, target.length));
    }
    if (this.shown.length < target.length && !this.timer) this.schedule();
  }

  /** Jump to the end (e.g. when the text is final). */
  flush(): void {
    this.stop();
    this.shown = this.target;
  }

  stop(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  private schedule(): void {
    this.timer = setTimeout(() => this.tick(), this.tickMs);
  }

  private tick(): void {
    this.timer = null;
    const left = this.target.length - this.shown.length;
    if (left <= 0) return;
    const step = Math.max(2, Math.ceil(left / this.catchUp));
    this.shown = this.target.slice(0, this.shown.length + step);
    if (this.shown.length < this.target.length) this.schedule();
  }
}
