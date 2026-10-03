import { Injectable } from '@angular/core';
import { BehaviorSubject, Subject } from 'rxjs';
import { SpeechToTextProvider } from './speech/provider';
import { WebSpeechProvider } from './speech/web-speech.provider';

@Injectable({ providedIn: 'root' })
export class TranscriptionService {
  transcript$ = new Subject<{ text: string; isFinal: boolean }>();
  interim$ = new BehaviorSubject<string>('');
  private provider: SpeechToTextProvider | null = null;
  private running = false;

  connect(provider?: SpeechToTextProvider): SpeechToTextProvider {
    this.provider = provider ?? new WebSpeechProvider();
    this.provider.onTranscript((t) => {
      this.transcript$.next(t);
      if (!t.isFinal) this.interim$.next(t.text);
      else this.interim$.next('');
    });
    return this.provider;
  }

  get activeProvider(): SpeechToTextProvider | null {
    return this.provider;
  }

  isSupported(): boolean {
    const p = this.provider ?? new WebSpeechProvider();
    return p.isSupported();
  }

  start(): void {
    if (!this.provider) this.connect();
    this.running = true;
    this.provider!.start();
  }

  stop(): void {
    this.running = false;
    this.provider?.stop();
    this.interim$.next('');
  }

  isRunning(): boolean {
    return this.running;
  }

  pushManual(text: string): void {
    const t = text.trim();
    if (!t) return;
    this.transcript$.next({ text: t, isFinal: true });
  }

  /** Emits a canned demo transcript for users without a mic. */
  simulateDemo(): void {
    const lines = [
      'Hello, thanks for joining today.',
      'Can you explain the Saga pattern in microservices?',
      'That makes sense. What about hash maps versus trees?',
      'Tell me about a time you resolved a production incident.',
      'How would you solve two-sum with optimal complexity?',
    ];
    lines.forEach((line, i) => {
      setTimeout(() => this.transcript$.next({ text: line, isFinal: true }), 800 * (i + 1));
    });
  }
}
