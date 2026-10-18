import { environment } from '../../../environments/environment';
import type {
  AnalysisResult,
  CaptureSource,
  CapturedImage,
  ScreenshotError,
  ScreenshotModel,
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
      keyStored: Record<string, boolean>;
      /** Where each provider's key comes from: saved in-app, or auto-loaded from .env. */
      keySources?: Record<string, 'stored' | 'env' | null>;
      /** Where to put a .env file for automatic key loading. */
      envHint?: string;
      encryptionAvailable: boolean;
    }>
  >;
  listSources(): Promise<BridgeResult<{ sources: CaptureSource[] }>>;
  capture(sourceId: string): Promise<BridgeResult<{ image: CapturedImage }>>;
  setKey(modelKey: string, apiKey: string): Promise<BridgeResult<object>>;
  clearKey(modelKey: string): Promise<BridgeResult<object>>;
  analyze(
    modelKey: string,
    images: Omit<CapturedImage, 'sourceName'>[],
  ): Promise<BridgeResult<{ result: AnalysisResult; model: string }>>;
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
