import { DatePipe } from '@angular/common';
import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AnalysisHistoryService } from '../../core/screenshot/analysis-history.service';
import { validateImages } from '../../core/screenshot/image-validation';
import {
  DEFAULT_SCREENSHOT_MODEL_KEY,
  ModelConfigService,
} from '../../core/screenshot/model-config.service';
import { ScreenCaptureService } from '../../core/screenshot/screen-capture.service';
import { ScreenshotAnalysisService } from '../../core/screenshot/screenshot-analysis.service';
import {
  AnalysisResult,
  CaptureSource,
  CapturedImage,
  HistoryEntry,
  ScreenshotError,
} from '../../core/screenshot/screenshot.models';
import { ErrorBannerComponent } from '../../shared/components/error-banner/error-banner.component';
import { AnalysisResultComponent } from './analysis-result.component';

export interface ShotItem {
  id: number;
  image: CapturedImage;
}

type PickerMode = { kind: 'add' } | { kind: 'replace'; index: number };

/**
 * Desktop-only: capture one or more screenshots of interview questions and get
 * answer guidance. Reachable only through `desktopOnlyGuard`.
 */
@Component({
  selector: 'app-screenshot-analysis',
  standalone: true,
  imports: [FormsModule, DatePipe, ErrorBannerComponent, AnalysisResultComponent],
  templateUrl: './screenshot-analysis.component.html',
  styleUrl: './screenshot-analysis.component.css',
})
export class ScreenshotAnalysisComponent implements OnInit, OnDestroy {
  private capture = inject(ScreenCaptureService);
  private analysis = inject(ScreenshotAnalysisService);
  readonly config = inject(ModelConfigService);
  readonly history = inject(AnalysisHistoryService);

  readonly defaultKey = DEFAULT_SCREENSHOT_MODEL_KEY;
  private nextId = 1;
  private timer: ReturnType<typeof setInterval> | null = null;

  readonly shots = signal<ShotItem[]>([]);
  readonly picker = signal<PickerMode | null>(null);
  readonly sources = signal<CaptureSource[]>([]);
  readonly loadingSources = signal(false);
  readonly capturing = signal(false);
  readonly captureNotice = signal('');
  readonly analyzing = signal(false);
  readonly elapsed = signal(0);
  readonly error = signal('');
  readonly result = signal<AnalysisResult | null>(null);
  readonly resultModel = signal('');
  readonly keyError = signal('');
  readonly keyBusy = signal(false);
  keyInput = '';

  readonly validation = computed(() =>
    this.shots().length
      ? validateImages(
          this.shots().map((s) => s.image),
          this.config.activeModel().limits,
        )
      : null,
  );
  readonly canAnalyze = computed(
    () =>
      !this.analyzing() &&
      this.config.hasKey() &&
      this.shots().length > 0 &&
      (this.validation()?.ok ?? false),
  );
  readonly atLimit = computed(() => this.shots().length >= this.config.activeModel().limits.maxImages);
  readonly totalMb = computed(() => ((this.validation()?.totalBytes ?? 0) / (1024 * 1024)).toFixed(1));

  async ngOnInit(): Promise<void> {
    try {
      await this.config.load();
    } catch (e) {
      this.error.set(this.message(e));
    }
  }

  ngOnDestroy(): void {
    this.stopTimer();
  }

  dataUrl(img: CapturedImage): string {
    return `data:${img.mimeType};base64,${img.data}`;
  }

  // ---- capture (always user-initiated: open picker → user chooses a source) ----

  async openPicker(mode: PickerMode): Promise<void> {
    this.error.set('');
    this.picker.set(mode);
    this.loadingSources.set(true);
    try {
      this.sources.set(await this.capture.listSources());
    } catch (e) {
      this.picker.set(null);
      this.error.set(this.message(e));
    } finally {
      this.loadingSources.set(false);
    }
  }

  closePicker(): void {
    if (!this.capturing()) this.picker.set(null);
  }

  async takeCapture(source: CaptureSource): Promise<void> {
    const mode = this.picker();
    if (!mode || this.capturing()) return;
    this.capturing.set(true);
    this.error.set('');
    try {
      const image = await this.capture.capture(source.id);
      this.shots.update((list) => {
        if (mode.kind === 'replace' && list[mode.index]) {
          return list.map((s, i) => (i === mode.index ? { id: s.id, image } : s));
        }
        return [...list, { id: this.nextId++, image }];
      });
      this.captureNotice.set(`Captured “${image.sourceName}” (${image.width}×${image.height})`);
      setTimeout(() => this.captureNotice.set(''), 3000);
      this.picker.set(null);
    } catch (e) {
      this.error.set(this.message(e));
    } finally {
      this.capturing.set(false);
    }
  }

  // ---- screenshot list controls ----

  remove(index: number): void {
    this.shots.update((list) => list.filter((_, i) => i !== index));
  }

  move(index: number, delta: -1 | 1): void {
    const to = index + delta;
    this.shots.update((list) => {
      if (to < 0 || to >= list.length) return list;
      const next = [...list];
      [next[index], next[to]] = [next[to], next[index]];
      return next;
    });
  }

  clearAll(): void {
    this.shots.set([]);
  }

  // ---- analysis ----

  async analyze(): Promise<void> {
    if (!this.canAnalyze()) return;
    this.error.set('');
    this.result.set(null);
    this.analyzing.set(true);
    this.elapsed.set(0);
    this.timer = setInterval(() => this.elapsed.update((n) => n + 1), 1000);
    try {
      const images = this.shots().map((s) => s.image);
      const { result, model } = await this.analysis.analyze(images);
      this.result.set(result);
      this.resultModel.set(model);
      this.history.add(result, model, images.length);
    } catch (e) {
      this.error.set(this.message(e));
    } finally {
      this.stopTimer();
      this.analyzing.set(false);
    }
  }

  openHistory(entry: HistoryEntry): void {
    this.result.set(entry.result);
    this.resultModel.set(entry.model);
  }

  // ---- AI model settings ----

  onModelChange(key: string): void {
    this.config.select(key);
    this.keyError.set('');
  }

  async saveKey(): Promise<void> {
    this.keyError.set('');
    this.keyBusy.set(true);
    try {
      await this.config.saveKey(this.keyInput);
      this.keyInput = '';
    } catch (e) {
      this.keyError.set(this.message(e));
    } finally {
      this.keyBusy.set(false);
    }
  }

  async removeKey(): Promise<void> {
    this.keyError.set('');
    try {
      await this.config.clearKey();
    } catch (e) {
      this.keyError.set(this.message(e));
    }
  }

  private stopTimer(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  private message(e: unknown): string {
    return e instanceof ScreenshotError ? e.message : 'Something went wrong. Please try again.';
  }
}
