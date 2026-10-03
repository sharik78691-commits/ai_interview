import { Injectable } from '@angular/core';
import { SpeechToTextProvider } from './provider';

declare global {
  interface Window {
    SpeechRecognition?: any;
    webkitSpeechRecognition?: any;
  }
}

@Injectable()
export class WebSpeechProvider implements SpeechToTextProvider {
  private rec: any = null;
  private cb: ((t: { text: string; isFinal: boolean }) => void) | null = null;

  isSupported(): boolean {
    return typeof window !== 'undefined' && !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  }

  onTranscript(cb: (t: { text: string; isFinal: boolean }) => void): void {
    this.cb = cb;
  }

  start(): void {
    if (!this.isSupported()) return;
    const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
    this.stop();
    this.rec = new Ctor();
    this.rec.continuous = true;
    this.rec.interimResults = true;
    this.rec.lang = 'en-US';
    this.rec.onresult = (ev: any) => {
      let interim = '';
      let finals = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i];
        if (r.isFinal) finals += r[0].transcript;
        else interim += r[0].transcript;
      }
      if (finals && this.cb) this.cb({ text: finals.trim(), isFinal: true });
      else if (interim && this.cb) this.cb({ text: interim.trim(), isFinal: false });
    };
    this.rec.onerror = () => { /* keep alive; consumer handles status */ };
    this.rec.onend = () => {
      // auto-restart if still intended to run
      if ((this as any)._running) {
        try { this.rec.start(); } catch { /* noop */ }
      }
    };
    (this as any)._running = true;
    try { this.rec.start(); } catch { /* noop */ }
  }

  stop(): void {
    (this as any)._running = false;
    try { this.rec?.stop(); } catch { /* noop */ }
    this.rec = null;
  }
}
