import { Injectable, signal } from '@angular/core';
import { AnalysisResult, HistoryEntry } from './screenshot.models';

const STORAGE_KEY = 'aia_screenshot_history';
/** Only the latest 3 analyses are kept (oldest dropped first). */
export const MAX_HISTORY = 3;

/**
 * Session history. Stored ONLY in this device's localStorage and holds text
 * results — never the screenshots themselves. There is no cloud sync in this
 * build, so nothing ever leaves the device through this service.
 */
@Injectable({ providedIn: 'root' })
export class AnalysisHistoryService {
  readonly entries = signal<HistoryEntry[]>(this.read());

  add(result: AnalysisResult, model: string, imageCount: number): HistoryEntry {
    const entry: HistoryEntry = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      createdAt: Date.now(),
      model,
      imageCount,
      result,
    };
    this.entries.update((list) => [entry, ...list].slice(0, MAX_HISTORY));
    this.write();
    return entry;
  }

  remove(id: string): void {
    this.entries.update((list) => list.filter((e) => e.id !== id));
    this.write();
  }

  clear(): void {
    this.entries.set([]);
    this.write();
  }

  private read(): HistoryEntry[] {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]');
      return Array.isArray(parsed) ? parsed.slice(0, MAX_HISTORY) : [];
    } catch {
      return [];
    }
  }

  private write(): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(this.entries()));
    } catch {
      // Storage full/disabled — history just won't persist.
    }
  }
}
