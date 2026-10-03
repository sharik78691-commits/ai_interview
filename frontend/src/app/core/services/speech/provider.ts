export interface SpeechToTextProvider {
  start(): void;
  stop(): void;
  onTranscript(cb: (t: { text: string; isFinal: boolean }) => void): void;
  isSupported(): boolean;
}
