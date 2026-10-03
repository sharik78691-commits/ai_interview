import { Injectable } from '@angular/core';
import { Observable, Subject, debounceTime, filter, map } from 'rxjs';

const WH_WORDS = ['what', 'why', 'how', 'when', 'where', 'who', 'which', 'explain', 'describe', 'tell me'];

@Injectable({ providedIn: 'root' })
export class QuestionDetectorService {
  private fragments$ = new Subject<string>();
  completeQuestions$: Observable<string>;

  constructor() {
    let buffer = '';
    this.completeQuestions$ = this.fragments$.pipe(
      map((f) => {
        buffer = (buffer + ' ' + f).trim();
        return buffer;
      }),
      debounceTime(1200),
      filter((buf) => this.isQuestion(buf)),
      map((buf) => {
        buffer = '';
        return buf.trim();
      }),
      filter((q) => q.length > 0),
    );
  }

  pushFragment(text: string): void {
    if (text.trim()) this.fragments$.next(text.trim());
  }

  isQuestion(text: string): boolean {
    const t = text.trim().toLowerCase();
    if (t.length < 8) return false;
    if (text.includes('?')) return true;
    return WH_WORDS.some((w) => t.includes(w));
  }

  /** Synchronous helper used in unit tests. */
  static detect(question: string): boolean {
    const t = question.trim().toLowerCase();
    if (t.length < 8) return false;
    if (question.includes('?')) return true;
    return WH_WORDS.some((w) => t.includes(w));
  }
}
