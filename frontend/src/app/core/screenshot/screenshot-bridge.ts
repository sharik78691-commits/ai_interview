import { environment } from '../../../environments/environment';
import type {
  AnalysisResult,
  CaptureSource,
  CapturedImage,
  ScreenshotError,
  ScreenshotModel,
  ScreenshotUsage,
} from './screenshot.models';

type Fail = { ok: false; error: { code: string; message: string } };
type Ok<T> = { ok: true } & T;
export type BridgeResult<T> = Ok<T> | Fail;

/** Shape of `window.electronAPI.screenshot` (see electron/preload.js). */
export interface ScreenshotBridge {
  getConfig(): Promise<
    BridgeResult<{
      models: ScreenshotModel[];
      defaultModel: string;
      /**
       * The AI key lives only on the server (GEMINI_API_KEY in its .env).
       * true/false = server is / is not set up; null = server not reachable now.
       */
      serverReady: boolean | null;
      /** Caller's screenshot credits; null/absent = unknown (older builds lack it). */
      usage?: ScreenshotUsage | null;
    }>
  >;
  listSources(): Promise<BridgeResult<{ sources: CaptureSource[] }>>;
  capture(sourceId: string): Promise<BridgeResult<{ image: CapturedImage }>>;
  analyze(
    modelKey: string,
    images: Omit<CapturedImage, 'sourceName'>[],
  ): Promise<BridgeResult<{ result: AnalysisResult; model: string; usage?: ScreenshotUsage | null }>>;
  /** Register / unregister the Ctrl+Shift+S capture hotkey (older builds lack it). */
  setHotkey?(enabled: boolean): Promise<BridgeResult<{ active: boolean }>>;
  /** Hotkey pressed: screen source under the cursor, or an error. Returns an unsubscribe fn. */
  onHotkey?(cb: (sourceId: string | null, error: { code: string; message: string } | null) => void): () => void;
  /**
   * Hotkey pressed while another page is open: show the Screenshots page and
   * open its picker on `sourceId` (screen under the cursor). Returns an unsubscribe fn.
   */
  onOpenPage?(cb: (sourceId: string | null) => void): () => void;
}

/**
 * The ONE place that decides whether screen capture exists. It requires BOTH
 * the desktop build flag and the Electron preload bridge, so the web build can
 * never expose it — even if someone fakes `window.electronAPI` in a browser,
 * the flag is compiled to `false` there.
 */
export function getScreenshotBridge(): ScreenshotBridge | null {
  if (!environment.electron) return null;
  return (typeof window !== 'undefined' && window.electronAPI?.screenshot) || null;
}

export const isScreenCaptureAvailable = (): boolean => getScreenshotBridge() !== null;

export type { ScreenshotError };
