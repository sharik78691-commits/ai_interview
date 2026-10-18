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

  async capture(sourceId: string): Promise<CapturedImage> {
    const res = await this.bridge().capture(sourceId);
    if (!res.ok) throw new ScreenshotError(res.error.code, res.error.message);
    return res.image;
  }
}
