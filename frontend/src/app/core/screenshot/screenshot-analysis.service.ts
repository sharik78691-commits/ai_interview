import { Injectable, inject } from '@angular/core';
import { validateImages } from './image-validation';
import { ModelConfigService } from './model-config.service';
import { getScreenshotBridge } from './screenshot-bridge';
import { AnalysisResult, CapturedImage, ScreenshotError } from './screenshot.models';

/** Sends validated screenshots to the configured model (via the main process). */
@Injectable({ providedIn: 'root' })
export class ScreenshotAnalysisService {
  private config = inject(ModelConfigService);

  async analyze(images: CapturedImage[]): Promise<{ result: AnalysisResult; model: string }> {
    const bridge = getScreenshotBridge();
    if (!bridge) {
      throw new ScreenshotError('NOT_DESKTOP', 'Screenshot analysis is only available in the desktop app.');
    }
    const model = this.config.activeModel();
    const check = validateImages(images, model.limits);
    if (!check.ok) {
      throw new ScreenshotError('VALIDATION', check.errors.map((e) => e.message).join(' '));
    }
    const res = await bridge.analyze(
      model.key,
      images.map(({ mimeType, data, width, height }) => ({ mimeType, data, width, height })),
    );
    if (!res.ok) throw new ScreenshotError(res.error.code, res.error.message);
    return { result: res.result, model: res.model };
  }
}
