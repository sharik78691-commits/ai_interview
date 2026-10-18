import { Injectable, signal } from '@angular/core';
import { environment } from '../../../environments/environment';

export const MAX_TRANSPARENCY = 88;
export const TRANSPARENCY_KEY = 'aia_window_transparency';

/**
 * Desktop window transparency (0% solid … 88% almost see-through), shared by
 * the whole app so the navbar slider works on every page. Same storage key as
 * the old interview-only slider, so saved values carry over.
 */
@Injectable({ providedIn: 'root' })
export class WindowTransparencyService {
  readonly percent = signal(0);

  constructor() {
    if (!environment.electron) return;
    this.percent.set(this.read());
    this.apply();
  }

  set(value: number): void {
    this.percent.set(this.clamp(value));
    try {
      localStorage.setItem(TRANSPARENCY_KEY, String(this.percent()));
    } catch {
      // Private mode / blocked storage — the slider still works this session.
    }
    this.apply();
  }

  clamp(value: number): number {
    if (!Number.isFinite(value)) return 0;
    return Math.min(MAX_TRANSPARENCY, Math.max(0, Math.round(value)));
  }

  private read(): number {
    try {
      const raw = localStorage.getItem(TRANSPARENCY_KEY);
      // Older versions allowed up to 100 — clamp into the current range.
      return this.clamp(raw === null ? 0 : Number(raw));
    } catch {
      return 0;
    }
  }

  /** Send 0–88% transparency to the Electron shell as 1.0–0.12 opacity. */
  private apply(): void {
    void window.electronAPI?.setOpacity?.(1 - this.percent() / 100);
  }
}
