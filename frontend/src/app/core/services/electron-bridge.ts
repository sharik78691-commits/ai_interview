/**
 * Types for the Electron bridge exposed on `window.electronAPI`.
 *
 * Present only when the app runs inside the Electron wrapper (see
 * `frontend/electron/preload.js`). In a normal browser `window.electronAPI` is
 * undefined, so every consumer must null-check it.
 */
import type { AIInterviewResponse } from '../models/interview.models';

export interface CopilotPayload {
  /** Latest interviewer utterance recognised from meeting/tab/system audio. */
  transcript: string;
  /** Latest AI guidance (null before the first question). */
  guidance: AIInterviewResponse | null;
}

export interface ElectronInvisibleAPI {
  /** Open (or focus) the capture-protected copilot window. */
  start(): Promise<boolean>;
  /** Close the copilot window. */
  stop(): Promise<boolean>;
  /** Push the current transcript + guidance to the copilot window. */
  update(payload: CopilotPayload): void;
  /** Fired when the user closes the copilot window directly. */
  onClosed(cb: () => void): void;
}

export interface ElectronAPI {
  isElectron: boolean;
  invisible: ElectronInvisibleAPI;
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI;
  }
}
