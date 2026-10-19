import { Injectable } from '@angular/core';
import { getScreenshotBridge } from './screenshot-bridge';
import { CaptureSource, CapturedImage, ScreenshotError } from './screenshot.models';

/**
 * Screen capture (desktop only). It never captures on its own: the UI lists
 * sources, the user picks one, and only then is `capture` called.
 */
@Injectable({ providedIn: 'root' })
export class ScreenCaptureService {
  get available(): boolean {
    return getScreenshotBridge() !== null;
  }

  private bridge() {
    const bridge = getScreenshotBridge();
    if (!bridge) {
      throw new ScreenshotError('NOT_DESKTOP', 'Screen capture is only available in the desktop app.');
    }
    return bridge;
  }

  async listSources(): Promise<CaptureSource[]> {
    const res = await this.bridge().listSources();
    if (!res.ok) throw new ScreenshotError(res.error.code, res.error.message);
    return res.sources;
  }

  /**
   * Put Ctrl+Shift+S in picker mode (screen under the cursor preselected) and
   * route presses to `onPress`. Returns a function that turns it off again.
   * No-op on builds whose bridge predates the hotkey.
   */
  async enableHotkey(
    onPress: (sourceId: string | null, error: ScreenshotError | null) => void,
  ): Promise<() => void> {
    const bridge = getScreenshotBridge();
    if (!bridge?.setHotkey || !bridge.onHotkey) return () => {};
    const unsubscribe = bridge.onHotkey((id, err) =>
      onPress(id, err ? new ScreenshotError(err.code, err.message) : null),
    );
    try {
      await bridge.setHotkey(true);
    } catch {
      // Registration is best-effort; the Capture button still works.
    }
    return () => {
      unsubscribe();
      bridge.setHotkey?.(false).catch(() => {});
    };
  }

  /**
   * Ctrl+Shift+S pressed while the Screenshots page is NOT open: call
   * `onRequest` (the app navigates there). No-op on the web build.
   */
  onOpenPageRequest(onRequest: () => void): () => void {
    return getScreenshotBridge()?.onOpenPage?.(onRequest) ?? (() => {});
  }

  async capture(sourceId: string): Promise<CapturedImage> {
    const res = await this.bridge().capture(sourceId);
    if (!res.ok) throw new ScreenshotError(res.error.code, res.error.message);
    return res.image;
  }
}
