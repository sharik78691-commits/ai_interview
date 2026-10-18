import { Injectable } from '@angular/core';
import { Observable, Subject, debounceTime, filter, map } from 'rxjs';

/**
 * Question cues in every form the interviewer actually speaks. English-only
 * here would mislabel almost every Hinglish/Hindi prompt — and Hindi is
 * wh-in-situ ("java mein kya hota hai"), so the cue sits in place rather
 * than leading the sentence; the word-boundary match handles that.
 */
const WH_WORDS = [
  'what', 'why', 'how', 'when', 'where', 'who', 'which', 'explain', 'describe', 'tell me',
  // Hinglish / Hindi (romanised)
  'kya', 'kaise', 'kaun', 'kab', 'kyu', 'kyun', 'kis', 'kitna', 'kahan',
  'batao', 'bataiye', 'bata', 'samjhao', 'samjhaiye', 'dikhao', 'bolo',
  'btao', 'bta', 'batado',
  // Devanagari
  'क्या', 'कैसे', 'कौन', 'कब', 'क्यों', 'किस', 'कितना', 'कहाँ',
  'बताइए', 'बताओ', 'समझाइए', 'दिखाइए',
];

/**
 * Word-boundary match, not substring. `String.includes` made "show the
 * data", "somewhere else" and "whichever one" look like questions because they
 * contain "how"/"where"/"which" — each of those fired a needless answer.
 *
 * Delimiters, NOT `\b`: JavaScript defines `\b` via `\w` = [A-Za-z0-9_], which
 * excludes Devanagari, so a Devanagari run contains no word boundary at all and
 * `\b`-based matching silently never matches it (Python's `\b` IS Unicode-aware,
 * which is why the same rule works on the backend). Explicit space/punctuation
 * delimiters work for every script.
 */
const WH_RE = new RegExp(
  `(?:^|[\\s,?!.'"()\\u0964])(?:${WH_WORDS.map(escapeRe).join('|')})(?=$|[\\s,?!.'"()\\u0964])`,
);

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

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
      // 600ms of silence = the human paused -> grab the COMPLETE buffered
      // question and answer immediately. Server fires "?"-terminated
      // questions itself and gates noise, so this speed is safe.
      debounceTime(600),
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
    return QuestionDetectorService.detect(text);
  }

  /** Synchronous helper used in unit tests. */
  static detect(question: string): boolean {
    const t = question.trim().toLowerCase();
    if (t.length < 8) return false;
    if (t.includes('?')) return true;
    return WH_RE.test(t);
  }
}
